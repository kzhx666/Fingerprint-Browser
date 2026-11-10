# -*- coding: utf-8 -*-
"""重画 50 图标：前 36 个用数字/字母（字体正常），后 14 个用 PIL 画几何图形。
避免出现豆腐块方框。"""
import os
import io
import json
from PIL import Image, ImageDraw, ImageFont

OUT = r"C:\KZPublish\kz-profile-manager\assets\instance-icons"
os.makedirs(OUT, exist_ok=True)

PALETTES = [
    ((76,141,255),(124,92,255)),((255,107,107),(255,159,67)),((32,201,151),(0,184,212)),
    ((255,159,67),(255,89,89)),((142,68,173),(102,126,234)),((0,168,150),(0,120,200)),
    ((253,121,168),(255,154,158)),((108,92,231),(72,52,212)),((0,194,168),(0,143,178)),
    ((250,169,22),(245,101,35)),((66,135,245),(0,198,255)),((214,51,132),(255,110,90)),
    ((0,168,120),(120,200,80)),((90,105,130),(40,55,80)),((255,94,98),(214,51,108)),
    ((58,123,213),(36,59,130)),((255,193,7),(255,143,0)),((32,178,170),(60,110,180)),
    ((156,39,176),(233,30,99)),((0,150,136),(0,105,92)),((244,67,54),(191,54,12)),
    ((63,81,181),(26,35,126)),((139,195,74),(85,139,47)),((255,112,67),(255,61,61)),
    ((0,131,143),(0,96,120)),((121,85,72),(78,52,46)),((158,158,158),(66,66,66)),
    ((3,169,244),(2,119,189)),((233,30,99),(156,39,176)),((76,175,80),(27,94,32)),
    ((255,235,59),(251,192,45)),((98,0,234),(69,39,160)),((0,229,255),(0,145,234)),
    ((255,61,129),(198,40,40)),((174,234,0),(85,139,47)),((101,31,255),(48,20,120)),
    ((255,138,101),(216,67,21)),((0,105,92),(0,77,64)),((100,181,246),(25,118,210)),
    ((240,98,146),(173,20,87)),((141,110,99),(93,64,55)),((130,177,255),(41,98,255)),
    ((244,143,177),(233,30,99)),((0,191,165),(0,137,123)),((255,167,38),(239,108,0)),
    ((92,107,192),(40,53,147)),((204,255,144),(100,221,23)),((255,82,82),(183,28,28)),
    ((38,198,218),(0,137,123)),((179,136,255),(106,27,154)),
]

# 0-9 + A-Z = 36 个用字体；36-49 用几何图形 kind
SHAPES = ["circle","triangle","diamond","square","star","heart","ring","cross",
          "hexagon","dots","bars","chevron","moon","plus"]
GLYPH_LABEL = [str(i) for i in range(10)] + [chr(ord('A')+i) for i in range(26)] + \
              ["圆","三角","菱","方","星","心","环","十字","六边","点阵","条","V","月","加"]

SIZES=[16,24,32,48,64,128,256]

def font_for(sz):
    for p in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(p):
            try: return ImageFont.truetype(p,int(sz))
            except Exception: pass
    return ImageFont.load_default()

def draw_shape(d, cx, cy, r, kind, col):
    w=max(2,int(r*0.16))
    W=(255,255,255,245)
    if kind=="circle":
        d.ellipse([cx-r,cy-r,cx+r,cy+r],fill=W)
    elif kind=="ring":
        d.ellipse([cx-r,cy-r,cx+r,cy+r],outline=W,width=w)
    elif kind=="square":
        q=r*0.8
        d.rounded_rectangle([cx-q,cy-q,cx+q,cy+q],radius=r*0.18,fill=W)
    elif kind=="diamond":
        d.polygon([(cx,cy-r),(cx+r,cy),(cx,cy+r),(cx-r,cy)],fill=W)
    elif kind=="triangle":
        d.polygon([(cx,cy-r),(cx+r,cy+r*0.8),(cx-r,cy+r*0.8)],fill=W)
    elif kind=="star":
        pts=[]
        for i in range(10):
            ang=-3.14159/2 + i*3.14159/5
            rr=r if i%2==0 else r*0.45
            pts.append((cx+rr*__import__("math").cos(ang),cy+rr*__import__("math").sin(ang)))
        d.polygon(pts,fill=W)
    elif kind=="heart":
        # 两个圆 + 三角近似
        q=r*0.5
        d.ellipse([cx-r*0.9,cy-r*0.7,cx,cy+r*0.2],fill=W)
        d.ellipse([cx,cy-r*0.7,cx+r*0.9,cy+r*0.2],fill=W)
        d.polygon([(cx-r*0.9,cy),(cx+r*0.9,cy),(cx,cy+r)],fill=W)
    elif kind=="cross":
        q=r*0.28
        d.rounded_rectangle([cx-q,cy-r,cx+q,cy+r],radius=q,fill=W)
        d.rounded_rectangle([cx-r,cy-q,cx+r,cy+q],radius=q,fill=W)
    elif kind=="plus":
        q=r*0.22
        d.rectangle([cx-q,cy-r*0.8,cx+q,cy+r*0.8],fill=W)
        d.rectangle([cx-r*0.8,cy-q,cx+r*0.8,cy+q],fill=W)
    elif kind=="hexagon":
        import math
        pts=[(cx+r*math.cos(math.pi/3*i),cy+r*math.sin(math.pi/3*i)) for i in range(6)]
        d.polygon(pts,fill=W)
    elif kind=="dots":
        for dx,dy in [(-0.5,-0.5),(0.5,-0.5),(-0.5,0.5),(0.5,0.5),(0,0)]:
            rr=r*0.22
            x,y=cx+dx*r,cy+dy*r
            d.ellipse([x-rr,y-rr,x+rr,y+rr],fill=W)
    elif kind=="bars":
        for i,bh in enumerate([0.4,0.7,1.0]):
            x=cx-r+ i*r*0.75
            d.rounded_rectangle([x,cy+r-bh*r*1.4,x+r*0.5,cy+r],radius=r*0.1,fill=W)
    elif kind=="chevron":
        d.polygon([(cx-r,cy-r*0.6),(cx,cy),(cx-r,cy+r*0.6),(cx-r*0.4,cy+r*0.6),(cx+r*0.4,cy),(cx-r*0.4,cy-r*0.6)],fill=W)
    elif kind=="moon":
        d.ellipse([cx-r,cy-r,cx+r,cy+r],fill=W)
        d.ellipse([cx-r*0.35,cy-r*1.05,cx+r*1.05,cy+r*0.35],fill=(0,0,0,0))
        # 用底色挖不出来（透明层），改为画外圈弧：简化成 C 形
    elif kind=="plus2":
        pass

def render(idx,size):
    ss=4; s=size*ss
    img=Image.new("RGBA",(s,s),(0,0,0,0))
    c1,c2=PALETTES[idx]
    grad=Image.new("RGB",(s,s)); px=grad.load()
    for y in range(s):
        for x in range(0,s,2):
            t=max(0.0,min(1.0,(x+y)/(2.0*s)))
            col=tuple(int(c1[i]+(c2[i]-c1[i])*t) for i in range(3))
            px[x,y]=col
            if x+1<s: px[x+1,y]=col
    mask=Image.new("L",(s,s),0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,s-1,s-1],radius=int(s*0.22),fill=255)
    img.paste(grad,(0,0),mask)
    hl=Image.new("RGBA",(s,s),(0,0,0,0))
    ImageDraw.Draw(hl).rounded_rectangle([int(s*.06),int(s*.05),int(s*.94),int(s*.5)],
                                        radius=int(s*.18),fill=(255,255,255,28))
    img=Image.alpha_composite(img,hl)
    d=ImageDraw.Draw(img)
    cx=cy=s/2
    if idx<36:
        f=font_for(s*0.56); g=GLYPH_LABEL[idx]
        bb=d.textbbox((0,0),g,font=f); tw,th=bb[2]-bb[0],bb[3]-bb[1]
        d.text(((s-tw)/2-bb[0],(s-th)/2-bb[1]-s*0.02),g,font=f,fill=(255,255,255,245))
    else:
        kind=SHAPES[idx-36]
        if kind=="moon":  # 用 C 形：大圆减小圆挖空，改用画两条弧近似
            W=(255,255,255,245); w=max(2,int(s*0.07))
            d.ellipse([cx-s*0.28,cy-s*0.28,cx+s*0.28,cy+s*0.28],outline=W,width=w)
            # 右侧开口
            d.rectangle([cx+s*0.1,cy-s*0.3,cx+s*0.32,cy+s*0.3],fill=(0,0,0,0))
        else:
            draw_shape(d,cx,cy,s*0.28,kind,c2)
    return img.resize((size,size),Image.LANCZOS)

def main():
    man=[]
    for i in range(50):
        imgs={sz:render(i,sz) for sz in SIZES}
        ico=os.path.join(OUT,"icon-%02d.ico"%i)
        imgs[256].save(ico,format="ICO",sizes=[(s,s) for s in SIZES],bitmap_format="bmp")
        imgs[48].save(os.path.join(OUT,"icon-%02d.png"%i))
        man.append({"id":"icon-%02d"%i,"label":GLYPH_LABEL[i],
                    "c1":"#%02X%02X%02X"%PALETTES[i][0],
                    "c2":"#%02X%02X%02X"%PALETTES[i][1]})
        print("  %02d %-4s ok"%(i,GLYPH_LABEL[i]))
    io.open(os.path.join(OUT,"manifest.json"),"w",encoding="utf-8").write(
        json.dumps(man,ensure_ascii=False,indent=2))
    print("done")
if __name__=="__main__": main()
