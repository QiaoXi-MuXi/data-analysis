# -*- coding: utf-8 -*-
"""本地提取电子发票/PDF/图片，按原模板填写物品验收单。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile

BASE = Path(__file__).resolve().parent
CENT = Decimal('0.01')
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}
DECIMAL_RE = re.compile(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')


def natural_key(name):
    """文件名/相对路径自然排序：2-1 排在 10-1 前，数字段按整数比较。"""
    text = str(name)
    key = []
    for part in re.split(r'(\d+)', text):
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part.casefold()))
    return (key, text.casefold())
# 低清图片先用标准放大；解析失败时按列出的顺序换预处理重试，仍须通过同样的票面校验。
OCR_PROFILES = {
    'standard': {'target': 3000, 'contrast': 0, 'sharpen': 0, 'gray': False},
    'enhanced': {'target': 3000, 'contrast': 1, 'sharpen': 140, 'gray': False},
    'detail':   {'target': 3600, 'contrast': 0, 'sharpen': 100, 'gray': True},
}
# 只处理数字字段里可能出现的形近字与全角数字，不做增删位数的猜测。
FULLWIDTH_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
OCR_CONFUSION = str.maketrans({
    '[': '1', ']': '1', '{': '1', '}': '1', '|': '1', '｜': '1',
    'l': '1', 'I': '1', '!': '1', 'O': '0', 'o': '0', 'Q': '0',
    'S': '5', 'B': '8', 'Z': '2', 'z': '2',
})
DECORATIONS = '[](){}|｜<>「」『』\'"`,、;；'
# 本机实测：单引擎 4/4 线程串行最快，多 worker 并发会因 CPU 争抢变慢。
# 默认串行；换到核心数更多的机器可用 --workers N 开启并行（每个线程独占一套引擎）。
MAX_OCR_WORKERS = 1


class InvoiceError(ValueError):
    """输入不完整或校验失败，禁止生成部分报销单。"""


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


def lines_of(boxes):
    """按几何位置重建行，避免 PDF 的内部文字顺序混乱。"""
    lines = []
    height = statistics.median(b.y1-b.y0 for b in boxes if b.text.strip())
    for box in sorted(boxes, key=lambda b: (b.cy, b.x0)):
        if not box.text.strip():
            continue
        candidates = [line for line in lines[-4:]
                      if abs(sum(b.cy for b in line) / len(line) - box.cy)
                      <= max(2, height*.7)]
        if candidates:
            min(candidates, key=lambda line: abs(line[0].cy-box.cy)).append(box)
        else:
            lines.append([box])
    return [sorted(line, key=lambda b: b.x0) for line in lines]


def text_of(boxes):
    return ''.join(b.text for b in sorted(boxes, key=lambda b: b.x0)).replace(' ', '')


def char_boxes(boxes):
    """OCR 词框仅用于定位表头；数值仍使用完整识别结果。"""
    chars = []
    for b in boxes:
        for i, ch in enumerate(b.text):
            if ch.isspace():
                continue
            w = (b.x1-b.x0) / len(b.text)
            chars.append(Box(ch, b.x0+i*w, b.y0, b.x0+(i+1)*w, b.y1, b.score))
    return chars


def locate(line, label):
    chars = sorted(char_boxes(line), key=lambda b: b.x0)
    text = ''.join(b.text for b in chars)
    pos = text.find(label)
    if pos < 0:
        raise InvoiceError(f'无法定位表头「{label}」；请检查票据清晰度或版式。')
    return chars[pos].x0, chars[pos+len(label)-1].x1


def number_forms(text):
    """把 OCR 数值文本规范成候选形式：仅去装饰符、还原形近字，不增删数字位。"""
    t = str(text).strip().replace(' ', '').replace('\u3000', '')
    t = t.replace('，', ',').replace('．', '.').replace('。', '.')
    t = t.replace('−', '-').replace('－', '-')
    t = re.sub(r'[¥￥元]', '', t).translate(FULLWIDTH_DIGITS)
    fixed = t.translate(OCR_CONFUSION)
    forms = []
    for candidate in (t, t.strip(DECORATIONS), fixed, fixed.strip(DECORATIONS)):
        if candidate and candidate not in forms:
            forms.append(candidate)
    return forms


def decimal_cell(text, label, allow_empty=False, check=None, extra=(), trace=None):
    """严格解析数值；有校验函数时，只允许唯一通过校验的候选值。

    extra 是同一单元格的其它识别结果（不同裁剪边界、整行拼接串去前缀等）。
    没有校验上下文时，任何形式必须指向同一个数，否则拒绝，避免静默改数。
    """
    raw = str(text)
    if not raw.strip() and allow_empty:
        return None
    base_forms = number_forms(raw)
    base = base_forms[0] if base_forms else ''
    forms = []
    for source in (raw, *extra):
        for form in number_forms(source):
            if form not in forms:
                forms.append(form)
    matches = []
    for form in forms:
        if not DECIMAL_RE.fullmatch(form):
            continue
        value = Decimal(form.replace(',', ''))
        if check is not None and not check(value):
            continue
        if any(value == old for _, old in matches):
            continue
        matches.append((form, value))
    if not matches:
        hint = '，且没有候选通过票面金额校验' if check is not None else ''
        raise InvoiceError(f'{label}无法可靠识别：{raw!r}{hint}')
    if len(matches) > 1:
        values = [str(v) for _, v in matches]
        hint = '多个通过校验的候选值' if check is not None else '存在歧义候选'
        raise InvoiceError(f'{label}{hint} {values}，无法可靠识别：{raw!r}')
    form, value = matches[0]
    if trace is not None:
        trace.append({'field': label, 'raw': raw, 'value': str(value),
                      'form': form, 'repaired': form != base})
    return value


# 「腈」（月+青）在低清票面上常被 CTC 判成同声旁的高频形近字（晴/睛/腊/腩…），
# 有时整字漏掉。只在能构成已知材料/化工词形时归一，避免误伤「晴天」「眼睛」等普通词。
NAME_CONFUSABLES = '晴睛腊腩静清请倩蜻'


def normalize_name_confusables(text):
    text = re.sub(r'丁[' + NAME_CONFUSABLES + r']', '丁腈', text)
    return re.sub(r'[' + NAME_CONFUSABLES + r'](?=纶|棉|橡胶)', '腈', text)


def inserted_char(base, candidate):
    """candidate 是否仅比 base 多一个字；返回该字（唯一时），否则 None。"""
    if len(candidate) != len(base)+1:
        return None
    hits = {candidate[i] for i in range(len(candidate))
            if candidate[:i]+candidate[i+1:] == base}
    return next(iter(hits)) if len(hits) == 1 else None


def repair_name(raw, candidates, trace=None):
    """低清品名补全：多个独立预处理变体必须一致地给出同一「纯插入一字」结果才采用。

    品名没有金额等式可兜底，因此任何改写、位置歧义或证据不足都保留原值并标记复核，
    绝不静默增删汉字。形近字归一只用于对齐候选，不改变其它字符。
    """
    if not raw or not candidates:
        return raw, False
    base = normalize_name_confusables(raw)
    votes, variants = Counter(), []
    for candidate in candidates:
        text = normalize_name_confusables(str(candidate))
        if text == base or inserted_char(base, text) is None:
            continue
        votes[text] += 1
        variants.append({'recognized': str(candidate), 'normalized': text})
    if len(votes) != 1 or sum(votes.values()) < 2:
        return raw, False
    value = next(iter(votes))
    if trace is not None:
        trace.append({'field': '物品名称', 'raw': raw, 'value': value,
                      'votes': sum(votes.values()), 'variants': variants})
    return value, True


class Extractor:
    def __init__(self, profile='standard'):
        self.engine = None
        self.current_image = None
        self.profile = profile
        self.ocr_attempted = False

    @property
    def target(self):
        return OCR_PROFILES[self.profile]['target']

    def ocr(self, image):
        import numpy as np
        from PIL import Image, ImageFilter, ImageOps
        self.ocr_attempted = True
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # 明确使用随库附带的模型和 CPU，不联网拉取模型。
            # 4/4 线程实测比 2/2 快约 1.9 倍且逐框识别结果完全一致；
            # 保持默认 rec_batch_num=6，改成 1 虽更快但会改变个别识别文本。
            self.engine = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=4)
        profile = OCR_PROFILES[self.profile]
        image = ImageOps.exif_transpose(image).convert('RGB')
        # 低清票据用 Lanczos 放大到更长的边，比原来的双线性放大保留更多数字笔画。
        if max(image.size) < profile['target']:
            scale = profile['target']/max(image.size)
            image = image.resize((round(image.width*scale), round(image.height*scale)), Image.LANCZOS)
        if profile['gray']:
            image = ImageOps.grayscale(image).convert('RGB')
        if profile['contrast']:
            image = ImageOps.autocontrast(image, cutoff=profile['contrast'])
        if profile['sharpen']:
            image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=profile['sharpen'], threshold=2))
        self.current_image = image
        result, _ = self.engine(np.asarray(image), use_cls=True)
        if not result:
            raise InvoiceError('OCR 未检测到文字。')
        boxes = [Box(text, min(p[0] for p in quad), min(p[1] for p in quad),
                     max(p[0] for p in quad), max(p[1] for p in quad), float(score))
                 for quad, text, score in result]
        return boxes, image.width, 'OCR'

    def read_cell(self, x0, y0, x1, y1):
        import numpy as np
        from PIL import ImageOps
        # 纵向多留一点余量，避免低清图片上数字上下缘被裁掉；横向边界由调用方用多个偏移试探。
        image = self.current_image.crop((max(0, int(x0)), max(0, int(y0)-3),
                                        min(self.current_image.width, int(x1)), min(self.current_image.height, int(y1)+3)))
        ink = ImageOps.invert(image.convert('L')).point(lambda p: 255 if p > 80 else 0)
        bounds = ink.getbbox()
        if bounds is None:
            return ''
        image = ImageOps.expand(image.crop(bounds), border=8, fill='white')
        result, _ = self.engine(np.asarray(image), use_det=False, use_cls=False)
        return ''.join(row[0] for row in result) if result else ''

    def name_candidates(self, x0, y0, x1, y1):
        """低置信品名的独立二次识别：同一区域用几种预处理各跑一次纯识别。

        全图 OCR 内部会缩到 2000 像素，而这里直接用 3000 像素原图裁剪，笔画更完整；
        多个变体共同出现同一个字，才作为补字依据（判定见 repair_name）。
        """
        import numpy as np
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        image = self.current_image
        base = image.crop((max(0, int(x0)-6), max(0, int(y0)-8),
                           min(image.width, int(x1)+6), min(image.height, int(y1)+8)))
        variants = []
        ink = ImageOps.invert(base.convert('L')).point(lambda p: 255 if p > 80 else 0)
        bounds = ink.getbbox()
        if bounds:
            variants.append(ImageOps.expand(base.crop(bounds), border=20, fill='white'))
        up = base.resize((base.width*2, base.height*2), Image.LANCZOS)
        variants.append(ImageEnhance.Contrast(up).enhance(1.5))
        variants.append(up.filter(ImageFilter.UnsharpMask(radius=1.2, percent=150, threshold=2)))
        variants.append(ImageOps.autocontrast(ImageOps.grayscale(up), cutoff=1).convert('RGB'))
        texts = []
        for variant in variants:
            result, _ = self.engine(np.asarray(variant), use_det=False, use_cls=False)
            if result:
                texts.append(''.join(row[0] for row in result))
        return texts

    def pages(self, path):
        if path.suffix.lower() != '.pdf':
            from PIL import Image
            with Image.open(path) as image:
                for i in range(getattr(image, 'n_frames', 1)):
                    image.seek(i)
                    yield i+1, self.ocr(image.copy())
            return
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as doc:
            for i in range(len(doc)):
                page = doc[i]
                try:
                    width, height = page.get_size()
                    boxes = []
                    textpage = page.get_textpage()
                    try:
                        for k in range(textpage.count_chars()):
                            ch = textpage.get_text_range(k, 1)
                            if not ch.strip():
                                continue
                            x0, y0, x1, y1 = textpage.get_charbox(k)
                            boxes.append(Box(ch, x0, height-y1, x1, height-y0))
                    finally:
                        textpage.close()
                    text = ''.join(b.text for b in boxes)
                    if '项目名称' in text and '价税合计' in text:
                        yield i+1, (boxes, width, 'PDF文字层')
                    else:
                        bitmap = page.render(scale=min(4, self.target/max(width, height)))
                        try:
                            yield i+1, self.ocr(bitmap.to_pil().copy())
                        finally:
                            bitmap.close()
                finally:
                    page.close()


def parse_page(boxes, width, source, page_number, method, cell_reader=None, name_candidates=None):
    lines = lines_of(boxes)
    headers = [i for i, line in enumerate(lines) if '项目名称' in text_of(line)]
    if not headers:
        raise InvoiceError(f'{source} 第{page_number}页未找到标准发票明细表头。')
    invoices = []
    previous_end = 0
    for h_index, start in enumerate(headers):
        end = headers[h_index+1] if h_index+1 < len(headers) else len(lines)
        header = lines[start]
        unit = locate(header, '单位')
        qty = locate(header, '数量')
        price = locate(header, '单价')
        amount = locate(header, '金额')
        rate = locate(header, '税率')
        try:
            name_stop = locate(header, '规格型号')[0] - width*.008
        except InvoiceError:
            name_stop = unit[0] - width*.015
        qty_start = unit[1] + width*.015
        qty_stop, price_stop, amount_stop = qty[1]+width*.004, price[1]+width*.004, amount[1]+width*.004
        rate_stop = locate(header, '税额')[0] - width*.02
        if not name_stop < qty_start < qty_stop < price_stop < amount_stop < rate_stop:
            raise InvoiceError('表头列位置不完整或顺序异常。')
        total_index = next((i for i in range(start+1, end) if '价税合计' in text_of(lines[i])), None)
        if total_index is None:
            raise InvoiceError(f'{source} 第{page_number}页缺少价税合计。')
        total_text = text_of(lines[total_index])
        match = re.search(r'[（(]小写[）)]\s*[¥￥Y]?(-?[\d,]+(?:\.\d{1,2})?)', total_text)
        if not match:
            raise InvoiceError(f'无法读取价税合计小写金额：{total_text}')
        total = money(match.group(1).replace(',', ''))
        # 同一页可以上下排放多张独立发票，各自绑定其上方的号码。
        prefix = ''.join(text_of(line) for line in lines[previous_end:start])
        inv_match = re.search(r'发票号码[:：]?(\d{20}|\d{8})', prefix)
        if not inv_match:
            raise InvoiceError(f'{source} 第{page_number}页发票号码缺失或识别不完整。')
        invoice_no = inv_match.group(1)
        items = []
        summary = None
        pending_name = ''
        trace = []
        name_trace = []
        for line in lines[start+1:total_index]:
            left = text_of([b for b in line if b.x0 < qty_start])
            if left.startswith('合计') or left.startswith('合') and '计' in left:
                summary = line
                break
            name_boxes = [b for b in line if (b.x0+b.x1)/2 < name_stop]
            cells = [text_of([b for b in line if low <= (b.x0+b.x1)/2 < high])
                     for low, high in [(0, name_stop), (qty_start, qty_stop),
                                       (qty_stop, price_stop), (price_stop, amount_stop),
                                       (amount_stop, rate_stop), (rate_stop, width+1)]]
            name, q, p, a, tax_rate, tax = cells
            price_extra = []
            price_shifts = []
            if cell_reader and any(b.x0 < qty_stop < b.x1 for b in line):
                # 数量紧邻长小数单价时，低清图片常把两列合成一个词框。
                # 数量只取自身列的独立识别结果，不从拼接数字猜；单价先给零成本候选，
                # 只有解析不过关时才逐个做偏移重识别，命中唯一解即停。
                joined = sorted([b for b in line if b.x0 < qty_stop < b.x1], key=lambda b: b.x0)
                y0 = min(b.y0 for b in joined)
                y1 = max(b.y1 for b in joined)
                q = cell_reader(qty_start, y0, qty_stop, y1)
                p = cell_reader(qty_stop, y0, price_stop, y1)
                joined_text = ''.join(b.text for b in joined).replace(' ', '')
                for q_form in number_forms(q):
                    if DECIMAL_RE.fullmatch(q_form) and joined_text.startswith(q_form):
                        price_extra.append(joined_text[len(q_form):])
                price_extra.append(joined_text)
                price_shifts = [(qty_stop+s, y0, price_stop, y1)
                                for s in (-width*.014, -width*.010, -width*.006, -width*.003, width*.003)]
                cells[1:3] = [q, p]
            if not q and not p and not a:
                if name and items:
                    items[-1]['name'] += name
                elif name:
                    pending_name += name
                continue
            had_pending = bool(pending_name)
            name = pending_name+name
            pending_name = ''
            if not name or not q or not p or not a:
                raise InvoiceError(f'明细列缺失：{cells}，不会猜测数量或金额。')
            quantity = decimal_cell(q, '数量', trace=trace)
            before_tax = decimal_cell(a, '不含税金额', trace=trace)
            if quantity <= 0:
                raise InvoiceError('暂不支持数量非正的红字/折扣明细。')
            # 发票单价常有十余位小数，允许源票舍入产生一分钱差异。
            # 低清图片的单价候选必须满足该等式，且只允许唯一解，否则整张票据拒绝。
            def price_consistent(value):
                return abs(money(quantity*value)-money(before_tax)) <= CENT
            while True:
                try:
                    raw_price = decimal_cell(p, '不含税单价', check=price_consistent,
                                             extra=price_extra, trace=trace)
                    break
                except InvoiceError as exc:
                    # 歧义无法靠更多候选解决，直接拒绝。
                    if not price_shifts or '多个通过校验' in str(exc):
                        raise
                    price_extra.append(cell_reader(*price_shifts.pop(0)))
            if not price_consistent(raw_price):
                raise InvoiceError(f'{name} 数量×单价与金额不一致：{q} × {p} ≠ {a}')
            if tax in {'免税', '不征税', '***', '**', '*'} or not tax and tax_rate in {'免税', '不征税', '0%', '0.00%'}:
                tax_value = Decimal(0)
            else:
                tax_value = decimal_cell(tax, '税额', trace=trace)
            # 直接读取各行税额，不用总税额按比例估算。
            name_review = method == 'OCR' and min((b.score for b in name_boxes), default=1) < .95
            name_fixed = False
            # 只对低置信、单行单框的 OCR 品名做补全；多行/多框拼接的品名留给人工复核。
            if name_review and name_candidates is not None and not had_pending and len(name_boxes) == 1:
                box = name_boxes[0]
                try:
                    candidates = name_candidates(box.x0, box.y0, box.x1, box.y1)
                except Exception:
                    # 品名补全属增强功能，二次识别失败不影响金额校验与出单。
                    candidates = []
                repaired, name_fixed = repair_name(name, candidates, name_trace)
                if name_fixed:
                    name, name_review = repaired, False
            items.append({'name': name, 'quantity': quantity,
                          'name_review_needed': name_review, 'name_auto_corrected': name_fixed,
                          'price_before_tax': raw_price, 'amount_before_tax': money(before_tax),
                          'tax': money(tax_value), 'amount': money(before_tax+tax_value)})
        if not items:
            raise InvoiceError(f'发票 {invoice_no} 未识别到完整明细。')
        item_total = sum((x['amount'] for x in items), Decimal(0))
        if item_total != total:
            raise InvoiceError(f'发票 {invoice_no} 明细含税金额 {item_total} 与价税合计 {total} 不一致。')
        if summary:
            sum_amount = text_of([b for b in summary if price_stop <= (b.x0+b.x1)/2 < amount_stop])
            sum_tax = text_of([b for b in summary if rate_stop <= (b.x0+b.x1)/2])
            expected_amount = sum((x['amount_before_tax'] for x in items), Decimal(0))
            expected_tax = sum((x['tax'] for x in items), Decimal(0))
            if money(decimal_cell(sum_amount, '合计金额',
                                  check=lambda v: money(v) == expected_amount, trace=trace)) != expected_amount:
                raise InvoiceError('不含税金额合计校验失败。')
            if sum_tax not in {'***', '**', '*'} and money(decimal_cell(
                    sum_tax, '合计税额', check=lambda v: money(v) == expected_tax, trace=trace)) != expected_tax:
                raise InvoiceError('税额合计校验失败。')
        for item in items:
            item['unit_price'] = (item['amount']/item['quantity']).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_UP)
        invoices.append({'source': source, 'page': page_number, 'method': method,
                         'invoice_no': invoice_no, 'total': total, 'items': items,
                         'number_repairs': [entry for entry in trace if entry['repaired']],
                         'name_repairs': name_trace})
        previous_end = total_index+1
    return invoices


def chinese_money(value):
    value = money(value)
    if value < 0 or value >= Decimal('1000000000000'):
        raise InvoiceError('金额超出支持的大写转换范围。')
    digits = '零壹贰叁肆伍陆柒捌玖'
    def group(n):
        result = ''
        zero = False
        for power, unit in [(3, '仟'), (2, '佰'), (1, '拾'), (0, '')]:
            d = n // 10**power % 10
            if d:
                if zero and result:
                    result += '零'
                result += digits[d]+unit
                zero = False
            elif result:
                zero = True
        return result
    integer = int(value)
    result = ''
    zero = False
    for power, unit in [(8, '亿'), (4, '万'), (0, '')]:
        n = integer // 10**power % 10000
        if n:
            if result and (zero or n < 1000):
                result += '零'
            result += group(n)+unit
            zero = False
        elif result:
            zero = True
    result = (result or '零')+'元'
    fraction = int((value-int(value))*100)
    jiao, fen = divmod(fraction, 10)
    if not fraction:
        return result+'整'
    if jiao:
        result += digits[jiao]+'角'
    elif integer:
        result += '零'
    if fen:
        result += digits[fen]+'分'
    return result


def workbook_info(template):
    with ZipFile(template) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(z.read('xl/workbook.xml'))
        first = workbook.find('s:sheets/s:sheet', NS)
        rid = first.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        sheet_path = target.lstrip('/') if target.startswith('/') else 'xl/'+target
        xml = z.read(sheet_path).decode('utf-8')
        root = ET.fromstring(xml)
        def value(cell):
            if cell is None:
                return ''
            if cell.get('t') == 's':
                return strings[int(cell.find('s:v', NS).text)]
            return ''.join(cell.find('s:is', NS).itertext()) if cell.get('t') == 'inlineStr' else cell.findtext('s:v', '', NS)
        rows = root.findall('s:sheetData/s:row', NS)
        if 'B4:C4' not in [m.get('ref') for m in root.findall('s:mergeCells/s:mergeCell', NS)]:
            raise InvoiceError('模板缺少 B4:C4 日期合并单元格。')
        expected = ['物品名称', '数量', '单价', '总值', '备注']
        for col, label in zip('ABCDE', expected):
            if label not in value(root.find(f"s:sheetData/s:row[@r='5']/s:c[@r='{col}5']", NS)):
                raise InvoiceError(f'模板第5行 {col} 列必须包含「{label}」。')
        total_row = next((int(row.get('r')) for row in rows if '合计金额' in value(row.find(f"s:c[@r='A{row.get('r')}']", NS))), None)
        if total_row is None or total_row <= 6:
            raise InvoiceError('模板未找到合计金额行。')
        # 模板含复杂附属对象时，不冒险移动这些对象。
        return sheet_path, xml, total_row, first.get('name')


def enable_wrap(styles_xml, style_index):
    """给物品名称列所用样式加上自动换行，其它样式字节保持不变。"""
    block = re.search(r'(<cellXfs\b[^>]*>)(.*?)(</cellXfs>)', styles_xml, re.S)
    if not block or style_index is None:
        return styles_xml
    body = block.group(2)
    starts = [m.start() for m in re.finditer(r'<xf\b', body)]
    idx = int(style_index)
    if idx >= len(starts):
        return styles_xml
    begin = starts[idx]
    end = starts[idx+1] if idx+1 < len(starts) else len(body)
    xf = body[begin:end]
    # shortcut: 依赖明细样式自带 <alignment>；若模板去掉该元素需改为补建 alignment。
    if 'wrapText' in xf or '<alignment ' not in xf:
        return styles_xml
    new_body = body[:begin]+xf.replace('<alignment ', '<alignment wrapText="1" ', 1)+body[end:]
    return styles_xml[:block.start()]+block.group(1)+new_body+block.group(3)+styles_xml[block.end():]


def cell_style(row_xml, ref):
    """取模板单元格的样式索引，用于统一明细区数字显示格式。"""
    match = re.search(rf'<c\b(?=[^>]*\br="{ref}")[^>]*\bs="(\d+)"', row_xml)
    return match.group(1) if match else None


def set_cell(row_xml, ref, value, style=None):
    pattern = rf'<c\b(?=[^>]*\br="{ref}")[^>]*(?:/>|>.*?</c>)'
    match = re.search(pattern, row_xml, re.S)
    if match:
        opening = re.match(r'<c\b[^>]*', match.group()).group().rstrip('/')
        opening = re.sub(r'\s+t="[^"]*"', '', opening)
        if style is not None:
            opening = re.sub(r'\s+s="[^"]*"', '', opening)+f' s="{style}"'
    else:
        # 模板缺少空单元格时复用同列邻居样式，由调用方保证标准模板。
        opening = f'<c r="{ref}"'+(f' s="{style}"' if style is not None else '')
    if value is None:
        content = opening+'/>'
    elif isinstance(value, (int, Decimal)):
        content = opening+'><v>'+str(value)+'</v></c>'
    else:
        content = opening+' t="inlineStr"><is><t xml:space="preserve">'+escape(str(value))+'</t></is></c>'
    if match:
        return row_xml[:match.start()]+content+row_xml[match.end():]
    # 插入到同一行正确的列序，保留模板其它列。
    next_cell = next((m for m in re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"', row_xml)
                      if (len(m.group(1)), m.group(1)) > (len(re.sub(r'\d', '', ref)), re.sub(r'\d', '', ref))), None)
    pos = next_cell.start() if next_cell else row_xml.rfind('</row>')
    return row_xml[:pos]+content+row_xml[pos:]


def shift_row(row_xml, new_row):
    row_xml = re.sub(r'(<row\b[^>]*\br=")\d+("?)', lambda m: m.group(1)+str(new_row)+m.group(2), row_xml, count=1)
    return re.sub(r'(<c\b[^>]*\br="[A-Z]+)\d+("?)', lambda m: m.group(1)+str(new_row)+m.group(2), row_xml)


def product_name(item):
    """物品名称保留原票完整文字，包括星号及税收分类前缀。"""
    item['display_name'] = item['name']
    return item['name']


def write_workbook(template, output, invoices, today=None):
    items = [item for inv in invoices for item in inv['items']]
    if not items:
        raise InvoiceError('没有可写入的明细。')
    sheet_path, xml, total_row, sheet_name = workbook_info(template)
    # 物品名称列（首列 A）明细单元格所用样式，写入 styles.xml 时给它加自动换行。
    a_style = re.search(r'<c\b[^>]*\br="A6"[^>]*\bs="(\d+)"', xml)
    a_style = a_style.group(1) if a_style else None
    total = sum((inv['total'] for inv in invoices), Decimal(0))
    extra = max(0, len(items)-(total_row-6))
    if extra and re.search(r'<(?:drawing|legacyDrawing|tableParts|conditionalFormatting|dataValidations)\b|<f\b', xml):
        raise InvoiceError('模板含公式或附属对象，超出预留明细行时无法安全扩展；请先在模板中增加空白明细行。')
    rows = {int(m.group(1)): m.group() for m in re.finditer(r'<row\b[^>]*\br="(\d+)"[^>]*(?:/>|>.*?</row>)', xml, re.S)}
    if any(r not in rows for r in range(6, total_row)):
        raise InvoiceError('模板明细区有缺失行，无法保持其样式。')
    new_rows = {}
    for r, row in rows.items():
        new_r = r+extra if r >= total_row else r
        new_rows[new_r] = shift_row(row, new_r) if new_r != r else row
    for r in range(total_row, total_row+extra):
        new_rows[r] = shift_row(rows[total_row-1], r)
    # 模板个别明细行可能被单独设成 3 位小数等格式，统一按首个明细行的样式，
    # 避免同一张验收单里单价/总值的显示位数不一致；字体、边框等仍保持各行原样。
    base_styles = {col: cell_style(rows[6], f'{col}6') for col in 'BCD'}
    for r in range(6, total_row+extra):
        item = items[r-6] if r-6 < len(items) else None
        values = [product_name(item), item['quantity'], item['unit_price'], item['amount'], None] if item else [None]*5
        for col, value in zip('ABCDE', values):
            new_rows[r] = set_cell(new_rows[r], f'{col}{r}', value, base_styles.get(col))
    today = today or date.today()
    new_rows[4] = set_cell(new_rows[4], 'B4', f'{today.year} 年 {today.month:02d} 月 {today.day:02d} 日')
    tr = total_row+extra
    new_rows[tr] = set_cell(new_rows[tr], f'B{tr}', chinese_money(total))
    new_rows[tr] = set_cell(new_rows[tr], f'E{tr}', total)
    new_xml = re.sub(r'<sheetData>.*?</sheetData>', '<sheetData>'+''.join(new_rows[r] for r in sorted(new_rows))+'</sheetData>', xml, flags=re.S)
    def shifted_ref(match):
        prefix, col1, r1, col2, r2, suffix = match.groups()
        a, b = int(r1), int(r2)
        return prefix+col1+str(a+extra if a >= total_row else a)+':'+col2+str(b+extra if b >= total_row else b)+suffix
    if extra:
        new_xml = re.sub(r'((?:ref|sqref)=")([A-Z]+)(\d+):([A-Z]+)(\d+)(")', shifted_ref, new_xml)
    # 不重新序列化整个工作簿；只修改目标 worksheet XML 的单元格内容。
    # styles 仅给物品名称列样式加自动换行，其余字体、行高、列宽、其它工作表和打印配置保持原始字节。
    ET.fromstring(new_xml)
    if output.exists() or output.resolve() == template.resolve():
        raise InvoiceError('输出已存在或指向模板；请使用新的输出文件名。')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.er_', suffix='.tmp', dir=output.parent)
    os.close(fd)
    try:
        with ZipFile(template) as source, ZipFile(temp_path, 'w') as target:
            for entry in source.infolist():
                content = new_xml.encode('utf-8') if entry.filename == sheet_path else source.read(entry.filename)
                if entry.filename == 'xl/styles.xml':
                    content = enable_wrap(content.decode('utf-8'), a_style).encode('utf-8')
                # 需要扩展打印区域时，仅平移该工作表的 Print_Area 终点。
                if extra and entry.filename == 'xl/workbook.xml':
                    content = content.decode('utf-8')
                    pattern = r'(<definedName\b[^>]*name="_xlnm.Print_Area"[^>]*localSheetId="0"[^>]*>)(.*?)(</definedName>)'
                    def shift_print(m):
                        area = re.sub(r'\$([A-Z]+)\$(\d+)', lambda n: '$'+n.group(1)+'$'+str(int(n.group(2))+extra if int(n.group(2)) >= total_row else int(n.group(2))), m.group(2))
                        return m.group(1)+area+m.group(3)
                    content = re.sub(pattern, shift_print, content).encode('utf-8')
                target.writestr(entry, content)
        # Windows 下 rename 不覆盖既有文件，避免重复运行或竞争写入覆盖成果。
        os.rename(temp_path, output)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return {'detail_count': len(items), 'invoice_count': len(invoices), 'total': total,
            'total_row': tr, 'sheet': sheet_name, 'date': today.isoformat()}


def unique_path(folder, stem, suffix):
    path = folder/(stem+suffix)
    i = 2
    while path.exists():
        path = folder/(stem+f'_{i}'+suffix)
        i += 1
    return path


def apply_name_corrections(invoices, path):
    """只应用有原识别文本保护条件的人工品名核对记录，金额不允许覆盖。"""
    if not path.exists():
        return
    corrections = json.loads(path.read_text(encoding='utf-8-sig'))
    for inv in invoices:
        for index, entry in corrections.get(inv['invoice_no'], {}).items():
            i = int(index)-1
            if i < 0 or i >= len(inv['items']):
                raise InvoiceError(f'人工核对记录明细序号超出范围：{index}')
            item = inv['items'][i]
            if item['name'] == entry['name']:
                continue
            if item['name'] != entry['recognized']:
                raise InvoiceError(f"发票 {inv['invoice_no']} 品名识别结果已变化，请复核 data/name_corrections.json。")
            item['recognized_name'] = item['name']
            item['name'] = entry['name']
            item['name_correction_reason'] = entry['reason']
            item['name_review_needed'] = False
            print(f"  已应用人工品名核对：{item['recognized_name']} → {item['name']}")


def needs_ocr(path):
    """预判是否要走 OCR：只有每页都有可用文字层的 PDF 才走快速路径。"""
    if path.suffix.lower() != '.pdf':
        return True
    import pypdfium2 as pdfium
    try:
        with pdfium.PdfDocument(path) as doc:
            for i in range(len(doc)):
                page = doc[i]
                try:
                    textpage = page.get_textpage()
                    try:
                        count = textpage.count_chars()
                        text = textpage.get_text_range(0, count) if count else ''
                    finally:
                        textpage.close()
                    if '项目名称' not in text or '价税合计' not in text:
                        return True
                finally:
                    page.close()
    except Exception:
        return True
    return False


def process_file(path, relative, extractor_factory):
    """识别单个票据；失败时按预处理档位重试。返回 (发票列表, 成功档位, 错误)。"""
    extractor = extractor_factory()
    for profile in OCR_PROFILES:
        extractor.profile = profile
        extractor.ocr_attempted = False
        used_ocr = False
        try:
            attempt = []
            for number, (boxes, width, method) in extractor.pages(path):
                used_ocr = used_ocr or method == 'OCR'
                attempt.extend(parse_page(boxes, width, relative, number, method,
                                          extractor.read_cell if method == 'OCR' else None,
                                          extractor.name_candidates if method == 'OCR' else None))
            for invoice in attempt:
                invoice['ocr_profile'] = profile
            return attempt, profile, None
        except Exception as exc:
            error = exc
            # 文字层 PDF 与预处理无关；非识别类异常也不靠重试解决。
            if not (used_ocr or extractor.ocr_attempted) or not isinstance(exc, InvoiceError):
                break
    return None, None, error


def run(args):
    data = args.data.resolve()
    if not data.is_dir():
        raise InvoiceError(f'找不到输入目录：{data}')
    templates = sorted(p for p in (BASE/'templates').glob('*.xlsx') if not p.name.startswith('~$'))
    template = args.template.resolve() if args.template else templates[0] if len(templates) == 1 else None
    if template is None:
        raise InvoiceError('templates 中必须只有一个 Excel 模板，多个模板时请用 --template 指定。')
    workbook_info(template)
    paths = sorted((p for p in data.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_TYPES | {'.pdf'}),
                   key=lambda p: natural_key(p.relative_to(data)))
    if not paths:
        raise InvoiceError('data 中没有 PDF 或支持的图片。')
    workers = max(1, int(getattr(args, 'workers', MAX_OCR_WORKERS)))
    extractor = Extractor()
    local = threading.local()
    def thread_extractor():
        # 每个工作线程独占一套 OCR 引擎，避免共享 ONNX 会话在并发时互相排队。
        if not hasattr(local, 'ex'):
            local.ex = Extractor()
        return local.ex
    def serial_extractor():
        return extractor

    # 先按原顺序查重，重复文件不进入识别队列，避免白跑 OCR。
    tasks, seen_files, errors_by_index = [], {}, {}
    for i, path in enumerate(paths):
        relative = str(path.relative_to(data))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen_files:
            errors_by_index[i] = f'{relative} 与 {seen_files[digest]} 内容重复，请将重复文件移出 data 后重跑。'
            continue
        seen_files[digest] = relative
        tasks.append((i, path, relative))

    parallel, ocr_tasks = False, []
    active_workers = 1
    if workers > 1:
        ocr_flags = {i: needs_ocr(path) for i, path, _ in tasks}
        ocr_tasks = [task for task in tasks if ocr_flags[task[0]]]
        parallel = len(ocr_tasks) >= 2
        active_workers = min(workers, len(ocr_tasks)) if parallel else 1
    results = {}
    if parallel:
        print(f'并行识别 {len(ocr_tasks)} 张票据（{active_workers} 线程）', flush=True)
        with ThreadPoolExecutor(max_workers=active_workers) as pool:
            pending = {pool.submit(process_file, path, relative, thread_extractor): i
                       for i, path, relative in ocr_tasks}
            for future in as_completed(pending):
                i = pending[future]
                try:
                    results[i] = future.result()
                except Exception as exc:
                    results[i] = (None, None, exc)
    for i, path, relative in tasks:
        if i not in results:
            results[i] = process_file(path, relative, serial_extractor)

    invoices, seen_invoices = [], {}
    for i, path, relative in tasks:
        print(f'读取：{relative}', flush=True)
        parsed, profile, error = results[i]
        if parsed is None:
            errors_by_index[i] = f'{relative}：{error}'
            continue
        if profile and profile != 'standard':
            print(f'  低清图像重试成功：{profile}', flush=True)
        try:
            for invoice in parsed:
                no = invoice['invoice_no']
                if no in seen_invoices:
                    raise InvoiceError(f'重复发票号码 {no}，已在 {seen_invoices[no]} 出现；不会重复报销。')
                seen_invoices[no] = relative
                print(f"  {no}：{len(invoice['items'])} 项，含税 {invoice['total']:.2f} 元（{invoice['method']}）", flush=True)
                for entry in invoice.get('number_repairs', []):
                    print(f"  低清数值校正（已通过票面校验）：{entry['field']} {entry['raw']!r} → {entry['value']}", flush=True)
                for entry in invoice.get('name_repairs', []):
                    print(f"  低清品名补全（{entry['votes']} 个独立识别一致）：{entry['raw']!r} → {entry['value']!r}，建议对照原票确认", flush=True)
            invoices.extend(parsed)
        except Exception as exc:
            errors_by_index[i] = f'{relative}：{exc}'
    errors = [errors_by_index[i] for i in sorted(errors_by_index)]
    if errors:
        raise InvoiceError('\n'.join(errors)+'\n本次未生成 Excel，请修正上述输入后重跑。')
    apply_name_corrections(invoices, data/'name_corrections.json')
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output = unique_path(output_dir, '物品验收单_'+stamp, '.xlsx')
    result = write_workbook(template, output, invoices)
    report = {'output': output.name, 'template': str(template.relative_to(data)) if template.is_relative_to(data) else template.name,
              **result, 'invoices': invoices, 'parallel_workers': active_workers,
              'amount_rule': '明细总值=原票不含税金额+该行税额；单价=含税总值/原票数量，保留12位计算精度。'}
    # 追溯文件独立放入已被项目忽略的 out，不改变 Excel 模板版式。
    report_dir = output_dir/'out'
    try:
        report_dir.mkdir(exist_ok=True)
        (report_dir/(output.stem+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    except OSError as exc:
        print(f'提示：Excel 已生成，但识别明细记录未保存：{exc}', file=sys.stderr)
    print(f"完成：{result['invoice_count']} 张发票，{result['detail_count']} 项，合计 {result['total']:.2f} 元。")
    print(f'输出：{output}')
    for inv in invoices:
        for item in inv['items']:
            if item.get('name_review_needed'):
                print(f"需复核图片品名：{inv['source']} / {item['name']}（金额已通过校验）")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=BASE/'data', help='票据与模板目录，默认脚本同级 data')
    parser.add_argument('--template', type=Path, help='明确指定 Excel 模板')
    parser.add_argument('--output-dir', type=Path, default=BASE, help='Excel 输出目录，默认 ER')
    parser.add_argument('--workers', type=int, default=MAX_OCR_WORKERS,
                        help=f'并行识别线程数，默认 {MAX_OCR_WORKERS}（串行最快）；核心数更多的机器可设为 2-4')
    parser.add_argument('--debug', action='store_true', help='出错时显示完整 traceback')
    parser.add_argument('--no-pause', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f'处理失败：{exc}', file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
