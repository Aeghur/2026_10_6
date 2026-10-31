"""Offline HSV extraction of a saved photograph; no camera or UART access."""
import argparse,json
from pathlib import Path
import cv2
import numpy as np


def extract(image,saturation_max=120,value_min=240,hue_min=0,hue_max=179):
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv,np.array([hue_min,0,value_min],np.uint8),
                      np.array([hue_max,saturation_max,255],np.uint8))


def canvas(mask):
    central=mask[:,80:880]
    resized=cv2.resize(central,(384,307),interpolation=cv2.INTER_AREA)
    result=np.zeros((384,384),np.uint8);result[38:345]=resized
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--s-max',type=int,default=120);ap.add_argument('--v-min',type=int,default=240)
    args=ap.parse_args();im=cv2.imread(str(args.input))
    if im is None:raise ValueError('Cannot read image')
    args.output.mkdir(parents=True,exist_ok=True)
    mask=extract(im,args.s_max,args.v_min)
    cv2.imwrite(str(args.output/'hsv_mask_full.png'),mask)
    cv2.imwrite(str(args.output/'hsv_384.png'),canvas(mask))
    selected=cv2.bitwise_and(im,im,mask=mask)
    cv2.imwrite(str(args.output/'hsv_selected_color.png'),selected)
    # Save a separate view excluding the known top interface band. This is
    # spatial masking, not a claimed effect of the HSV range.
    plot_mask=mask.copy();plot_mask[:45]=0
    cv2.imwrite(str(args.output/'plot_mask_full.png'),plot_mask)
    cv2.imwrite(str(args.output/'plot_384.png'),canvas(plot_mask))
    cv2.imwrite(str(args.output/'plot_selected_color.png'),cv2.bitwise_and(im,im,mask=plot_mask))
    settings=dict(input=str(args.input.resolve()),hue=[0,179],saturation=[0,args.s_max],
                  value=[args.v_min,255],hsv_scale='OpenCV H 0–179; S,V 0–255',
                  morphology=False,spatial_mask_in_plot_outputs_only='first 45 warped rows excluded',
                  original_strategy_modified=False)
    (args.output/'settings.json').write_text(json.dumps(settings,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
