# -*- coding: utf-8 -*-
"""真实高度数据的 MetroPro 样式重绘；不声称原生渲染或逐像素相同。"""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image, ImageDraw

def advance(ch):
    return 12 if ch=='+' else 7 if ch=='-' else 6 if ch=='.' else 11


class PixelFont:
    """从已知截图字符串提取数字字形；新数据标签由字形重新排版。"""
    def __init__(self, source):
        self.glyphs={}
        examples={'4-1':('+0.00664','-0.00748'), '3-4':('+0.00604','-0.00527'),
                  '3-1':('+0.00404','-0.00629'), '5-1':('+0.00419','-0.00425'),
                  '6-1':('+0.00780','-0.00414')}
        for stem,texts in examples.items():
            path=source/(stem+'.bmp')
            if not path.exists():
                continue
            im=Image.open(path).convert('RGB').crop((669,75,1271,400))
            for text,ytop in zip(texts,[18,102]):
                self.add(im,text,509,ytop)
        # 横向单位及刻度中补齐数字 3；保持相同的 18 像素字形盒。
        ref=Image.open(source/'4-1.bmp').convert('RGB').crop((669,75,1271,400))
        self.add(ref,'0.0530',509,123)
        self.mm=ref.crop((211,282,245,300))
        self.um=ref.crop((509,63,537,81))
        if not set('0123456789.+-').issubset(self.glyphs):
            raise ValueError('原截图未能提供完整数字字形，不能静默改用其他字体')

    def add(self,im,text,x,y):
        for ch in text:
            width=advance(ch)
            block=np.asarray(im.crop((x,y,x+width,y+18)))
            mask=np.all(block[:,:,:3]>220,axis=2)
            # 保留基线，尤其不能把小数点、负号移到字形顶部。
            if mask.any():
                self.glyphs.setdefault(ch,Image.fromarray((mask*255).astype(np.uint8)))
            x+=width

    def text(self,im,xy,text,color=(255,255,255),scale=1):
        width=sum(advance(ch) for ch in text)
        mask=Image.new('L',(max(width,1),18),0)
        x=0
        for ch in text:
            if ch not in self.glyphs:
                raise ValueError(f'没有字形: {ch}')
            mask.paste(self.glyphs[ch],(x,0)); x+=advance(ch)
        if scale!=1:
            mask=mask.resize((max(1,round(width*scale)),max(1,round(18*scale))),Image.Resampling.NEAREST)
        im.paste(color,xy,mask)


class SmallFont:
    """原截图指标字段的 10 行点阵，保留小数点基线。"""
    patterns={
        '0':['.####.','#....#','#....#','#....#','#....#','#....#','#....#','#....#','#....#','.####.'],
        '1':['..##...','##.#...','...#...','...#...','...#...','...#...','...#...','...#...','...#...','#######'],
        '2':['.#####.','#.....#','#.....#','......#','.....#.','...##..','..#....','.#.....','#.....#','#######'],
        '3':['..####.','##....#','......#','......#','...###.','.....#.','......#','......#','#.....#','.#####.'],
        '4':['....#.','...##.','..#.#.','.#..#.','.#..#.','#...#.','######','....#.','....#.','..####'],
        '5':['.#####.','.#.....','.#.....','.#.....','.#####.','......#','......#','......#','#.....#','.#####.'],
        '6':['...####','..#....','.#.....','#......','#.####.','##....#','#.....#','#.....#','#.....#','.#####.'],
        '7':['#######','#.....#','......#','.....#.','.....#.','....#..','....#..','....#..','...#...','...#...'],
        '8':['.####.','#....#','#....#','#....#','.####.','#....#','#....#','#....#','#....#','.####.'],
        '9':['.####.','#....#','#....#','#....#','#...##','.###.#','.....#','....#.','...#..','###...'],
        '.':['.......']*8+['.##....','.##....'],
        '+':['.......']*2+['...#...','...#...','.#####.','...#...','...#...']+['.......']*3,
        '-':['.......']*4+['.#####.']+['.......']*5,
    }

    def __init__(self):
        self.glyphs={ch:Image.fromarray(np.array([[255 if p=='#' else 0 for p in row.ljust(7,'.')] for row in rows],dtype=np.uint8)) for ch,rows in self.patterns.items()}

    def text(self,im,xy,text,color=(255,255,255),scale=1):
        mask=Image.new('L',(max(len(text)*10,1),14),0)
        for i,ch in enumerate(text):
            mask.paste(self.glyphs[ch],(i*10,2))
        if scale!=1:
            # 缩小时先保留笔画覆盖率，再二值化，避免最近邻漏掉水平细笔画。
            mask=mask.resize((round(mask.width*scale),round(mask.height*scale)),Image.Resampling.LANCZOS)
            mask=mask.point(lambda p:255 if p>=80 else 0)
        im.paste(color,xy,mask)


def colorize(z,limits,lut):
    norm=(z-limits[0])/(limits[1]-limits[0])
    idx=np.clip(np.nan_to_num(norm)*len(lut),0,len(lut)-1).astype(int)
    rgb=lut[idx].copy(); rgb[~np.isfinite(z)]=0
    return rgb


def oblique(z,dx,limits,lut,font):
    """使用原图的轴向投影和像素尺寸，完整高度网格按远到近绘制。"""
    im=Image.new('RGB',(602,325),'black')
    draw=ImageDraw.Draw(im)
    h,w=z.shape
    xx=np.arange(w)/(w-1)
    v=1-np.arange(h)/(h-1)
    px=56+336*xx[None,:]+83*v[:,None]
    base=262-140*v[:,None]+np.zeros((h,w))
    py=base-94*(z-limits[0])/(limits[1]-limits[0])
    valid=np.isfinite(z)
    # 右侧灰色实体与正面垂线是原 Oblique 的 Baseline 显示特征。
    right=[(392,262),(475,122)]+[(float(px[i,-1]),float(py[i,-1])) for i in range(h) if valid[i,-1]]
    draw.polygon(right,fill=(128,128,128))
    for j in range(0,w,7):
        if valid[-1,j]:
            draw.line((float(px[-1,j]),262,float(px[-1,j]),float(py[-1,j])),fill=(128,128,128))
    # 面网格采用真实高度，不从截图反推或复制表面纹理。
    avg=(z[:-1,:-1]+z[:-1,1:]+z[1:,:-1]+z[1:,1:])/4
    rgb=colorize(avg,limits,lut)
    cells=valid[:-1,:-1]&valid[:-1,1:]&valid[1:,:-1]&valid[1:,1:]
    for i in range(h-1):
        for j in np.flatnonzero(cells[i]):
            points=[(float(px[i,j]),float(py[i,j])),(float(px[i,j+1]),float(py[i,j+1])),
                    (float(px[i+1,j+1]),float(py[i+1,j+1])),(float(px[i+1,j]),float(py[i+1,j]))]
            draw.polygon(points,fill=tuple(int(c) for c in rgb[i,j]))
    # 坐标、刻度、色条、字形都采用截图实际像素位置。
    draw.line((56,262,392,262),fill='white')
    for k in range(11):
        x=round(56+336*k/10); draw.line((x,262,x,269),fill='white')
    draw.line((392,262,475,122),fill='white')
    for k in range(7):
        x=round(392+83*k/6); y=round(262-140*k/6)
        draw.line((x,y,x+12,y),fill='white')
    bar=Image.fromarray(np.repeat(lut[::-1,None,:],10,axis=1)).resize((10,94),Image.Resampling.NEAREST)
    im.paste(bar,(475,28))
    draw=ImageDraw.Draw(im)
    draw.line((475,28,475,122),fill='white')
    for k in range(7):
        y=round(28+94*k/6); draw.line((475,y,487,y),fill='white')
    font.text(im,(509,18),f'{limits[1]/1000:+.5f}')
    font.text(im,(509,102),f'{limits[0]/1000:+.5f}')
    font.text(im,(25,282),'0.0000')
    font.text(im,(360,282),f'{w*dx/1000:.4f}')
    font.text(im,(426,249),'0.0000')
    font.text(im,(509,123),f'{h*dx/1000:.4f}')
    im.paste(font.mm,(211,282))
    im.paste(font.mm,(509,179))
    im.paste(font.um,(509,63))
    return im

class NativeFont:
    def __init__(self,kind):
        self.kind=kind;self.glyphs={}

    def advance(self,ch):
        if self.kind=='map':return {'+':9,'-':5,'.':5}.get(ch,8)
        return {'-':5,'.':4}.get(ch,8)

    def add(self,im,text,x,y):
        for ch in text:
            w=self.advance(ch)
            pixels=np.asarray(im.crop((x,y,x+w,y+14)))
            mask=np.all(pixels>220,axis=2)
            if mask.any():self.glyphs.setdefault(ch,Image.fromarray((mask*255).astype('uint8')))
            x+=w

    def text(self,im,xy,text):
        x,y=xy
        for ch in text:
            im.paste('white',(x,y),self.glyphs[ch]);x+=self.advance(ch)


class MetricFont:
    def __init__(self,style):
        self.glyphs={}
        for ch,rows in style.SmallFont.patterns.items():
            mask=Image.new('L',(10,10),0)
            a=np.array([[255 if p=='#' else 0 for p in row.ljust(7,'.')] for row in rows],dtype='uint8')
            bearing=2 if ch=='.' else 0 if ch in '25' else 1
            mask.paste(Image.fromarray(a),(bearing,0));self.glyphs[ch]=mask

    def text(self,im,xy,text):
        x,y=xy
        for ch in text:
            im.paste('white',(x,y),self.glyphs[ch]);x+=10

    def right(self,im,right,y,text):self.text(im,(right-len(text)*10,y),text)

    def add(self,im,text,x,y):
        for ch in text:
            pixels=np.asarray(im.crop((x,y,x+10,y+10)))
            self.glyphs[ch]=Image.fromarray((np.all(pixels>220,axis=2)*255).astype('uint8'));x+=10


def color_fit(z,limits,spectrum):
    bounds=np.quantile(z[np.isfinite(z)],[.005,.995],method='linear')
    if bounds[1]<=bounds[0]:bounds=np.array(limits)
    # 色条仍覆盖真实最小/最大高度；超出两个分位点只饱和显示。
    heights=np.linspace(*limits,2000)
    u=np.clip((heights-bounds[0])/(bounds[1]-bounds[0]),0,1)
    lut=np.column_stack([np.interp(u*1999,np.arange(2000),spectrum[:,c]) for c in range(3)]).round().astype('uint8')
    return lut,bounds


def profile_plot(reference,z,dx,ticks,metric):
    im=reference.crop((185,479,741,825));d=ImageDraw.Draw(im)
    line=z[z.shape[0]//2];valid=np.isfinite(line)
    actual_lo,actual_hi=np.nanmin(line),np.nanmax(line)
    span=max(actual_hi-actual_lo,.01);unit=10.**np.floor(np.log10(span))
    lo=np.floor((actual_lo-span*.025)/unit)*unit;hi=np.ceil((actual_hi+span*.025)/unit)*unit
    d.rectangle((26,40,530,220),fill=reference.getpixel((212,520)))
    d.rectangle((107,49,515,215),fill='black')
    d.line((100,49,100,215),fill='white')
    for k in range(21):
        yy=round(49+166*k/20);d.line((94 if k%5==0 else 97,yy,100,yy),fill='white')
    for k in range(5):
        yy=round(49+166*k/4);value=(hi-(hi-lo)*k/4)/1000
        ticks.text(im,(26,yy-5),f'{value:+.5f}')
    # 重写横轴；位置沿用原界面，数值使用本文件标定。
    d.rectangle((96,229,542,249),fill=reference.getpixel((212,520)))
    for fraction in [0,.02,.04,.06]:
        # 原图每 0.02 mm 一格；推广为视场的四等分。
        t=fraction/.08
        ticks.text(im,(96+round(408*t),231),f'{(len(line)-1)*dx/1000*t:.4f}')
    if lo<=0<=hi:
        yy=round(215-166*(0-lo)/(hi-lo))
        for x in range(107,516,20):d.line((x,yy,min(x+12,515),yy),fill='white')
    points=[]
    for j,value in enumerate(line):
        if np.isfinite(value):points.append((107+408*j/(len(line)-1),215-166*(value-lo)/(hi-lo)))
        elif points:d.line(points,fill=(0,255,0));points=[]
    if points:d.line(points,fill=(0,255,0))
    centered=line[valid]-line[valid].mean()
    stats=dict(PV_nm=float(np.ptp(line[valid])),Rq_nm=float(np.sqrt(np.mean(centered**2))),Ra_nm=float(np.mean(abs(centered))))
    for box,value,right,y in [((90,282,188,301),stats['PV_nm'],160,288),((90,310,188,329),stats['Rq_nm'],160,316),((365,282,467,301),stats['Ra_nm'],440,288)]:
        d.rectangle(box,fill='black');metric.right(im,right,y,f'{value/1000:.3f}')
    return im,dict(row_zero_based=z.shape[0]//2,limits_nm=[float(lo),float(hi)],metrics=stats,display_decimals=3)


def panels(reference,z,intensity,dx,limits,lut,fonts,stats,style,cutoff=None,oblique_image=None):
    big,mapfont,ticks,metric=fonts
    surface=reference.crop((185,25,666,414));d=ImageDraw.Draw(surface)
    surface.paste(reference.crop((670,27,815,44)),(169,2))
    surface.paste(Image.fromarray(style.colorize(z,limits,lut)).resize((320,240),Image.Resampling.NEAREST),(19,40))
    # 截面位置只画在原截图外侧标记与图框位置，不覆盖真实数据区。
    bar=Image.fromarray(np.repeat(lut[::-1,None,:],10,axis=1)).resize((10,215),Image.Resampling.NEAREST)
    surface.paste(bar,(354,52))
    bg=reference.getpixel((560,110))
    d.rectangle((368,43,477,66),fill=bg);d.rectangle((368,254,477,280),fill=bg)
    mapfont.text(surface,(372,45),f'{limits[1]/1000:+.5f}');mapfont.text(surface,(372,259),f'{limits[0]/1000:+.5f}')
    for y,value in [(311,stats['Sz_nm']),(339,stats['Sq_nm']),(367,stats['Sa_nm'])]:
        d.rectangle((190,y-5,278,y+11),fill='black');metric.right(surface,270,y,f'{value:.3f}')
    oblique=style.oblique(z,dx,limits,lut,big) if oblique_image is None else oblique_image
    obwin=reference.crop((666,47,1274,404));obwin.paste(oblique,(3,28))
    profile,pmeta=profile_plot(reference,z,dx,ticks,metric)
    intensity_window=reference.crop((742,479,1116,791))
    gray=np.clip(np.nan_to_num(intensity),0,255).astype('uint8')
    intensity_window.paste(Image.fromarray(gray).resize((320,240),Image.Resampling.NEAREST),(28,47))
    # 保留原四窗的实际相对位置及原来的字段条带，不再留下大块空白。
    canvas=reference.crop((185,25,1274,825))
    canvas.paste(surface,(0,0));canvas.paste(obwin,(481,22));canvas.paste(profile,(0,454));canvas.paste(intensity_window,(557,454))
    canvas.paste(reference.crop((185,25,1274,47)),(0,0))
    cd=ImageDraw.Draw(canvas)
    # 原截图右下角有另一个控制窗口的一小部分，四图重建不携带其旧参数。
    cd.rectangle((985,482,1088,799),fill=reference.getpixel((1300,400)))
    # 本次没有高通时不能遗留原 80 µm 标签；有候选高通时才填实际截止值。
    cd.rectangle((875,420,1018,436),fill='black')
    if cutoff is not None:metric.right(canvas,1000,422,f'{cutoff:.5f}')
    for y,value in [(396,z.shape[1]*dx/1000),(424,z.shape[0]*dx/1000)]:
        cd.rectangle((190,y-5,278,y+12),fill='black');metric.right(canvas,270,y,f'{value:.2f}')
    images=dict(surface_map=surface,oblique_plot=oblique,oblique_window=obwin,surface_profile=profile,intensity_map=intensity_window,four_panel=canvas)
    return images,pmeta



def load_assets():
    """只加载随代码附带的静态资源，运行时不读取参考截图。"""
    with np.load(Path(__file__).with_name('zygo_style_assets.npz'),allow_pickle=False) as data:
        reference=Image.fromarray(data['template'])
        big=PixelFont.__new__(PixelFont);big.glyphs={}
        big.mm=Image.fromarray(data['font_mm']);big.um=Image.fromarray(data['font_um'])
        mapfont=NativeFont('map');ticks=NativeFont('ticks')
        metric=MetricFont.__new__(MetricFont);metric.glyphs={}
        fonts=(big,mapfont,ticks,metric)
        for i,font in enumerate(fonts):
            prefix=f'g{i}_'
            font.glyphs={chr(int(key[len(prefix):])):Image.fromarray(data[key]) for key in data.files if key.startswith(prefix)}
        return reference,data['spectrum'].copy(),fonts


def render(z,intensity,dx,stats,assets,cutoff=None,label_style=None):
    reference,spectrum,fonts=assets
    limits=[float(np.nanmin(z)),float(np.nanmax(z))]
    if limits[1]<=limits[0]:limits=[limits[0]-.005,limits[1]+.005]
    lut,bounds=color_fit(z,limits,spectrum)
    from plot_labels import settings, annotate
    label_style=settings(label_style, enabled=False)
    oblique_image=oblique(z,dx,limits,lut,fonts[0])
    annotate(oblique_image, f'Sa={stats["Sa_nm"]:.3f} nm', label_style)
    # 光强显示采用文件头范围归一化后的 0..255；缺测点保持黑色。
    images,profile=panels(reference,z,intensity,dx,limits,lut,fonts,stats,
                           SimpleNamespace(colorize=colorize,oblique=oblique),cutoff,oblique_image=oblique_image)
    return images,dict(profile=profile,display_limits_nm=limits,color_fit_quantiles_nm=bounds.tolist(),label_style=label_style)
