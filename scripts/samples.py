"""Generate ZIP inputs from repository samples; never special-case sample IDs in the backend."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    for sample in sorted((ROOT / 'samples').iterdir()):
        if not sample.is_dir():
            continue
        archive = sample.with_suffix('.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for file in sorted(sample.rglob('*')):
                if file.is_file() and not any(part in ('node_modules', 'dist') for part in file.relative_to(sample).parts):
                    z.write(file, sample.name + '/' + file.relative_to(sample).as_posix())
        print(archive.name, archive.stat().st_size, 'bytes')

if __name__ == '__main__':
    main()
