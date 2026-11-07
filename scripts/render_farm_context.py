"""Scientific raster layers in the screenshot coordinate plane (not image generation)."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import json
import numpy as np
import rasterio
from rasterio import Affine
from rasterio.warp import reproject,Resampling
from PIL import Image
from matplotlib import colormaps
from agro_checker.farm_context_data import D,R,read

ref=read(R/'satellite-reference.json');M=np.array(read(R/'registration.json')['matrix_source_to_reference']);e=ref['extent']
sx=(e['xmax']-e['xmin'])/ref['width'];sy=(e['ymax']-e['ymin'])/ref['height']
dst_transform=Affine(M[0,0]*sx,M[0,1]*sx,e['xmin']+M[0,2]*sx,-M[1,0]*sy,-M[1,1]*sy,e['ymax']-M[1,2]*sy)
for key,path,cmap,lo,hi in [('terrain',D/'dem/surface.tif','terrain',0,100),('clay',D/'soil/clay_0-5cm_mean.tif','YlOrBr',15,55),('ph',D/'soil/phh2o_0-5cm_mean.tif','viridis',5.5,8.5)]:
    dst=np.full((2560,1244),np.nan,dtype='float32')
    with rasterio.open(path) as r:
        a=r.read(1,masked=True).astype('float32').filled(np.nan)
        if key!='terrain':a/=10
        reproject(a,dst,src_transform=r.transform,src_crs=r.crs,dst_transform=dst_transform,dst_crs='EPSG:3857',src_nodata=np.nan,dst_nodata=np.nan,resampling=Resampling.nearest)
    rgba=(colormaps[cmap](np.clip((dst-lo)/(hi-lo),0,1))*255).astype('uint8');rgba[:,:,3]=np.where(np.isfinite(dst),225,0).astype('uint8')
    Image.fromarray(rgba).save(ROOT/f'web/farm-reconstruction/context-{key}.png')
    scale=(colormaps[cmap](np.linspace(0,1,256)[None,:])*255).astype('uint8')
    Image.fromarray(np.repeat(scale,12,axis=0)).save(ROOT/f'web/farm-reconstruction/context-{key}-scale.png')
    print('Rendered',key,'valid',int(np.isfinite(dst).sum()))
