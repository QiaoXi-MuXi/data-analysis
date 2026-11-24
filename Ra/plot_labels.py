# -*- coding: utf-8 -*-
"""出图附加数值标注；只改变像素，不参与测量与代表图选择。"""
from __future__ import annotations

import math
import re

POSITIONS = ('左上', '上方中间', '右上', '左下', '下方中间', '右下')
# 允许用户按习惯填写同义写法，统一折算到标准位置。
POSITION_ALIASES = {'上中': '上方中间', '中上': '上方中间', '上方居中': '上方中间', '上居中': '上方中间',
                    '顶部': '上方中间', '顶部居中': '上方中间', '顶部中间': '上方中间',
                    '下中': '下方中间', '中下': '下方中间', '下方居中': '下方中间', '下居中': '下方中间',
                    '底部': '下方中间', '底部居中': '下方中间', '底部中间': '下方中间'}
DEFAULTS = dict(label_size=44, label_color='#FFFFFF', label_position='左上',
                label_stroke=3, label_stroke_color='#000000')
EDGE_X, EDGE_TOP, EDGE_BOTTOM = 10, 4, 10  # 贴边留白：左右边距与上、下边距（像素）
MIN_SIZE = 8            # 与配置列的下限一致，字宽超限时不小于该值


def integer(value, default, lower, upper, title):
    if value is None or str(value).strip() == '':
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{title}需要 {lower}–{upper} 的整数') from None
    if isinstance(value, bool) or not math.isfinite(number) or not number.is_integer() or not lower <= number <= upper:
        raise ValueError(f'{title}需要 {lower}–{upper} 的整数')
    return int(number)


def color(value, default, title):
    text = str(value).strip() if value is not None and str(value).strip() else default
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', text):
        raise ValueError(f'{title}请填写 #RRGGBB，例如 #FFFFFF')
    return text.upper()


def settings(values=None, *, enabled=True):
    values = values or {}
    switch = values.get('label_enabled')
    if switch is None or str(switch).strip() == '':
        switch = enabled
    elif switch in (0, 1):
        switch = bool(switch)
    elif str(switch).strip().casefold() in ('1', '1.0', '启用', '是', 'on', 'true', 'yes', 'y'):
        switch = True
    elif str(switch).strip().casefold() in ('0', '0.0', '关闭', '否', 'off', 'false', 'no', 'n'):
        switch = False
    else:
        raise ValueError('数值标注开关请填写 0 或 1')
    position = str(values.get('label_position') or '').strip()
    position = POSITION_ALIASES.get(position, position) or DEFAULTS['label_position']
    if position not in POSITIONS:
        raise ValueError('标注位置请选择：' + '、'.join(POSITIONS))
    return dict(label_enabled=switch,
                label_size=integer(values.get('label_size'), 44, 8, 96, '标注字号'),
                label_color=color(values.get('label_color'), '#FFFFFF', '标注颜色'),
                label_position=position,
                label_stroke=integer(values.get('label_stroke'), 3, 0, 8, '描边宽度'),
                label_stroke_color=color(values.get('label_stroke_color'), '#000000', '描边颜色'))


def font(size):
    from PIL import ImageFont
    for name in ('msyhbd.ttc', 'msyh.ttc', 'simhei.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def annotate(image, text, style=None):
    """原位添加文字；默认坐标和字形与原 BMP 导出一致。关闭时不改任何像素。"""
    from PIL import ImageDraw
    style = settings(style)
    if not style['label_enabled'] or not text:
        return image
    draw = ImageDraw.Draw(image)
    stroke = style['label_stroke']
    size = style['label_size']
    available = image.width - 2 * EDGE_X
    face = font(size)
    box = draw.textbbox((0, 0), text, font=face, stroke_width=stroke)
    # 兼容任意字号：只有字宽超出左右边距时才收敛到能完整放下的最大字号，不裁字。
    if available > 0 and box[2] - box[0] > available:
        size = max(MIN_SIZE, min(size - 1, int(size * available / (box[2] - box[0]))))
        face = font(size)
        box = draw.textbbox((0, 0), text, font=face, stroke_width=stroke)
        while size > MIN_SIZE and box[2] - box[0] > available:
            size -= 1
            face = font(size)
            box = draw.textbbox((0, 0), text, font=face, stroke_width=stroke)
    position = style['label_position']
    x, y = EDGE_X, EDGE_TOP
    if '下' in position:
        y = max(0, image.height - EDGE_BOTTOM - box[3])
    if '右' in position:
        x = max(0, image.width - EDGE_X - box[2])
    elif '中' in position:
        # 以实际字形墨迹宽度居中，忽略字号自带的左右侧边距。
        x = max(0, round((image.width - (box[2] - box[0])) / 2) - box[0])
    draw.text((x, y), text, font=face, fill=style['label_color'],
              stroke_width=stroke, stroke_fill=style['label_stroke_color'])
    return image
