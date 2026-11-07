"""Reproduce relative registration; never fetches or modifies operational data.

Writes a NEW audit result by default, leaving the reviewed registration frozen.
Requires optional requirements-farm-reconstruction.txt (or tmp/georef-deps).
"""
from pathlib import Path
import json
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tmp/georef-deps'))
import cv2

D=ROOT/'data/farm-research/reconstruction'
src=cv2.imread(str(ROOT/'docs/assets/farm-reference/farm-map-user-provided.png'),cv2.IMREAD_GRAYSCALE)
ref=cv2.imread(str(D/'satellite-reference.jpg'),cv2.IMREAD_GRAYSCALE)
mask=np.zeros_like(src);mask[380:2050,:]=255
sift=cv2.SIFT_create(nfeatures=25000)
ks,ds=sift.detectAndCompute(src,mask)
kr,dr=sift.detectAndCompute(ref,None)
bf=cv2.BFMatcher()
forward={a.queryIdx:a.trainIdx for a,b in bf.knnMatch(ds,dr,k=2) if a.distance<.6*b.distance}
reverse={a.queryIdx:a.trainIdx for a,b in bf.knnMatch(dr,ds,k=2) if a.distance<.6*b.distance}
matches=[(i,j) for i,j in forward.items() if reverse.get(j)==i]
x=np.float32([ks[i].pt for i,j in matches]);y=np.float32([kr[j].pt for i,j in matches])
reserved=((np.floor(x[:,0]/200)+2*np.floor(x[:,1]/200))%4)==0
M,inliers=cv2.estimateAffinePartial2D(x[~reserved],y[~reserved],method=cv2.RANSAC,ransacReprojThreshold=3,maxIters=10000,confidence=.999)
pred=np.c_[x,np.ones(len(x))]@M.T;err=np.linalg.norm(pred-y,axis=1)
result={'matrix_source_to_reference':M.tolist(),'partition':'((floor(x/200)+2*floor(y/200)) mod 4)==0 reserved before fit','match_filter':'mutual SIFT, descriptor ratio <0.60 both directions','ransac_threshold_reference_px':3,'source_size':[src.shape[1],src.shape[0]],'reference_size':[ref.shape[1],ref.shape[0]],'pairs':[dict(source=a.tolist(),reference=b.tolist(),reserved=bool(r),residual_reference_px=float(e)) for a,b,r,e in zip(x,y,reserved,err)],'metrics':{'matched':len(matches),'train':int(np.sum(~reserved)),'reserved':int(np.sum(reserved)),'training_ransac_inliers':int(np.sum(inliers,dtype=np.int64))}}
for label,subset in [('reserved',reserved),('train',~reserved)]:
 for metric,value in [('median',np.median(err[subset])),('p95',np.quantile(err[subset],.95)),('max',max(err[subset])),('rmse',np.sqrt(np.mean(err[subset]**2)))]:
  result['metrics'][f'{label}_{metric}_reference_px']=float(value)
path=D/'registration-reproduced.json'
path.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result['metrics']))
