"""Build a source-only NAS package. Databases and private test artifacts stay local."""
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parent
output=root/'dist';output.mkdir(exist_ok=True)
archive=output/'mahjong-offline-nas.zip'
files=['README.md','DESIGN.md','Dockerfile','compose.yaml','requirements.txt','requirements.lock','.env.example','.dockerignore','.gitignore','package.py','tests/test_app.py','tests/fixtures.py','tests/__init__.py']
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
    for name in files:bundle.write(root/name,name)
    for folder in ['app','static']:
        for file in sorted((root/folder).rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts:bundle.write(file,file.relative_to(root).as_posix())
    bundle.writestr('data/.gitkeep','')
print('Package: '+str(archive))
print('Bytes: '+str(archive.stat().st_size))
