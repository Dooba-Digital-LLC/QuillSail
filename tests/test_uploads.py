from io import BytesIO

from PIL import Image
from werkzeug.datastructures import FileStorage

from copydesk.uploads import save_image


def test_png_upload_strips_exif_metadata(app):
    data = BytesIO()
    image = Image.new('RGB', (16, 16))
    exif = Image.Exif()
    exif[0x010E] = 'PRIVATE CAMERA METADATA'
    image.save(data, format='PNG', exif=exif)
    data.seek(0)
    with app.app_context():
        upload = save_image(FileStorage(stream=data, filename='camera.png'), None)
        path = app.extensions['copydesk'].config.uploads_dir / upload.path
        with Image.open(path) as stored:
            assert not stored.getexif()
        assert b'PRIVATE CAMERA METADATA' not in path.read_bytes()


def test_animated_upload_is_reencoded_without_private_metadata(app):
    data = BytesIO()
    image = Image.new('RGB', (16, 16), 'red')
    second = Image.new('RGB', (16, 16), 'blue')
    image.save(data, format='GIF', save_all=True, append_images=[second], duration=[120, 240],
               loop=0, comment=b'PRIVATE ANIMATION METADATA')
    data.seek(0)
    with app.app_context():
        upload = save_image(FileStorage(stream=data, filename='animation.gif'), None)
        path = app.extensions['copydesk'].config.uploads_dir / upload.path
        with Image.open(path) as stored:
            assert stored.n_frames == 2
            assert stored.info['duration'] == 120
            stored.seek(1)
            assert stored.info['duration'] == 240
        assert b'PRIVATE ANIMATION METADATA' not in path.read_bytes()
