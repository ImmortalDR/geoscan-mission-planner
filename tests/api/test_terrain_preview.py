from pathlib import Path
from io import BytesIO
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from PIL import Image
from gmp.api.terrain_preview import terrain_preview


def test_preview_preserves_nodata_and_reprojects_geographic_input(tmp_path):
    path=tmp_path/'dem.tif'
    with rasterio.open(path,'w',driver='GTiff',height=2,width=2,count=1,dtype='float32',
                       crs='EPSG:4326',transform=from_origin(37,56,.001,.001),nodata=-9999) as out:
        out.write(np.array([[10,20],[30,-9999]],dtype='float32'),1)
    info,data=terrain_preview(path)
    assert info['min_m']==10 and info['max_m']==30
    assert info['coordinates'][0]==pytest.approx([37,56])
    assert info['coordinates'][2]==pytest.approx([37.002,55.998])
    with Image.open(BytesIO(data)) as image:
        pixels=np.array(image)
        assert pixels[0,0,3]==255 and pixels[-1,-1,3]==0
        assert not np.array_equal(pixels[0,0,:3],pixels[-1,0,:3])


def test_preview_rejects_vrt_and_all_nodata(tmp_path):
    path=tmp_path/'dem.tif'
    path.write_text('<VRTDataset/>')
    with pytest.raises(ValueError,match='TIFF'):terrain_preview(path)
    with rasterio.open(path,'w',driver='GTiff',height=2,width=2,count=1,dtype='float32',
                       crs='EPSG:32637',transform=from_origin(500000,6000000,30,30),nodata=-9999) as out:
        out.write(np.full((2,2),-9999,dtype='float32'),1)
    with pytest.raises(ValueError,match='no valid'):terrain_preview(path)
