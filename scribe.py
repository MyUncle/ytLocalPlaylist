from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover

from edict import Edict


def write_tags(base: Path, song: str, entry: Edict):
    filepath = base / song
    if not filepath.exists():
        return

    if entry.status is None:
        status = ''
    else:
        status = entry.status
    write_needed = False
    tags = MP4(filepath).tags
    tags['\xa9gen'] = 'Nightcore'
    tags['\xa9alb'] = song.split('.')[0]
    if entry.name is not None and 'N' not in status:
        tags['\xa9nam'] = entry.name
        status += "N"
        write_needed = True
    if entry.artist is not None and 'A' not in status:
        tags['\xa9ART'] = entry.artist
        status += "A"
        write_needed = True
    if entry.image is not None and 'P' not in status:
        ipath = Path(entry.image)
        if ipath.exists():
            with open(ipath, 'rb') as ifile:
                idata = ifile.read()
            cover = MP4Cover(idata)
            tags['covr'] = [cover]
            print(f'Set cover - {song}')
            status += 'P'
        write_needed = True
    if write_needed:
        tags.save(filepath)
        entry.status = status

