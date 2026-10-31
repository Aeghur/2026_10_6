import numpy as np

def foreground(image):
    array = np.asarray(image)
    if array.ndim==3:
        array = array[:,:,:3].max(axis=2)
    if array.ndim!=2 or min(array.shape)<32:
        raise ValueError('Expected a calibrated grayscale/RGB plotting bitmap, at least 32px')
    mask = array > max(75,float(np.percentile(array,99.5))*.45)
    # Reject isolated hot/noise pixels, retain connected one-pixel curves.
    pad = np.pad(mask,1)
    neighbors = np.zeros(mask.shape,np.uint8)
    for dy in range(3):
        for dx in range(3):
            if (dy,dx)!=(1,1):
                neighbors += pad[dy:dy+mask.shape[0],dx:dx+mask.shape[1]]
    mask &= neighbors>=2
    return mask

