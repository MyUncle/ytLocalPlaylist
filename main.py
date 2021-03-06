import json
import os
import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import List, Optional

import PySimpleGUI as sg

import scribe
from art import ArtRetriever
from artist import ArtistRetriever
from config import Config
from meta import Meta
from playlist import Playlist
from songdb import SongDB
import urllib3


def open_dl_window(num_downloads: int) -> sg.Window:
    layout = [[
        sg.Frame(f"DL-{i}", k=f"DL-{i}", layout=[
            [sg.ProgressBar(100, 'horizontal', size=(15, 4), bar_color=('#e0bbe4', '#957dad'), k=f'PROG-{i}')]
        ])]
        for i in range(num_downloads)
    ]
    # noinspection PyTypeChecker
    layout.append([
        sg.Button('Cancel', size=(20, 2), k="DL Cancel")
    ])
    window = sg.Window("Downloads", disable_minimize=True, grab_anywhere=True, no_titlebar=True, layout=layout,
                       finalize=True)
    return window


def prepare_pl_frame(pl: Playlist) -> List[sg.Element]:
    total, present, missing, lost, void = pl_status(pl)
    return [sg.Frame(pl.name, k=pl.name, layout=[
        [sg.T('Total:               ', size=(15, 1), text_color='#ace7ff'), sg.T(total, k=(pl, 'total'))],
        [sg.T('Present:             ', size=(15, 1), text_color='#dbffd6'), sg.T(present, k=(pl, 'present'))],
        [sg.T('Missing:             ', size=(15, 1), text_color='#ffb7b2'), sg.T(missing, k=(pl, 'missing'))],
        [sg.T('Lost - Recoverable:  ', size=(15, 1), text_color='#ff9aa2'), sg.T(lost, k=(pl, 'lost'))],
        [sg.T('Lost - Unrecoverable:', size=(15, 1), text_color='#440033'), sg.T(void, k=(pl, 'void'))]
    ]), sg.Frame('Actions', k=f'{pl.name}-Actions', layout=[
        [sg.B('Download Missing', k=(pl, 'download'))],
        [sg.B('Copy to Playlist Directory', k=(pl, 'copy'))]
    ])]


def pl_status(pl):
    db = SongDB()
    found, fail = pl.retrieve_playlist_meta()
    total = len(found) + len(fail)
    present = len([x for x in found if x in db.files])
    missing = total - present
    lost = len([x for x in fail if x in db.files])
    void = len(fail) - lost
    return total, present, missing, lost, void


def main():
    db = SongDB()
    config = Config()
    meta = Meta()
    art: Optional[ArtRetriever] = None
    artist: Optional[ArtistRetriever] = None
    tp = ThreadPoolExecutor(config.jobs)

    playlists = [Playlist(**pl) for pl in config.config.playlists]

    layout = [prepare_pl_frame(pl) for pl in playlists]
    layout.append([
        sg.Button("Update Database", size=(20, 2), k="DB Update"),
        sg.Button("Name Tool", size=(20, 2), k="Open Name Tool"),
        sg.Button("Art Tool", size=(20, 2), k="Open Art Tool"),
        sg.Button("Artist Tool", size=(20, 2), k="Open Artist Tool")
    ])

    main_window = sg.Window("YT Local Playlist Manager", resizable=True,
                            layout=layout, finalize=True)

    print(f'Launching App with config - \n {json.dumps(config.config, indent=4)}')

    dl_queue = Queue()
    dl_window: Optional[sg.Window] = None
    dl_pl: Optional[Playlist] = None
    futures = []

    meta_wind: Optional[sg.Window] = None
    art_wind: Optional[sg.Window] = None
    artist_wind: Optional[sg.Window] = None

    while True:
        window, event, values = sg.read_all_windows(2000, "TIMEOUT")

        if window is None and event is None and values is None:
            break
        elif event == sg.WIN_CLOSED:
            if window is meta_wind:
                meta_wind = None
            elif window is art_wind:
                art = None
                art_wind = None
            elif window is artist_wind:
                artist = None
                artist_wind = None
            window.close()
        # region Tagged Events
        elif event == 'TIMEOUT':
            if len(futures) > 0 and all(fut.done() for fut in futures):
                for fut in futures:
                    if fut.exception() is not None:
                        raise fut.exception()
                dl_window.close()
                dl_window = None
                futures.clear()
                t, p, m, l, v = pl_status(dl_pl)
                updates = [(t, 'total'), (p, 'present'), (m, 'missing'), (l, 'lost'), (v, 'void')]
                for val, key in updates:
                    main_window[(dl_pl, key)].update(val)
                dl_pl = None
        elif event == "DL Cancel":
            while not dl_queue.empty():
                try:
                    dl_queue.get_nowait()
                except queue.Empty:
                    continue
        elif event == "DB Update":
            for pl in playlists:
                for song in pl.playlist_meta:
                    if song.videoId not in db.db:
                        db.add_song(song)
            db.save()
        elif event == "Open Name Tool":
            if meta_wind is None:
                meta_wind = meta.get_window()
        elif event == "Open Art Tool":
            if art is None:
                art = ArtRetriever()
                art_wind = art.get_window()
        elif event == "Open Artist Tool":
            if art is None:
                artist = ArtistRetriever()
                artist_wind = artist.get_window()

        # endregion Tagged Events

        if type(event) is not tuple:
            continue

        ctx, action = event
        # region Playlist Events
        if type(ctx) == Playlist:
            pl: Playlist
            pl = ctx
            if action == "copy":
                songs, _ = pl.results
                mtime = os.path.getmtime(pl.file)
                print(mtime)
                pl.song_ids = []
                for entry, song in [(db.db[name], f'{name}.m4a') for name in songs if name in db.files]:
                    pl.song_ids.append(song)
                    if entry.status is None or len([flag for flag in "NAP" if flag not in entry.status]) > 0:
                        scribe.write_tags(db.songPath, song, entry)
                    if not Path.exists(pl.location / song):
                        os.link(db.songPath / song, pl.location / song)
                    elif os.path.getmtime(db.songPath / song) > mtime:
                        print(f'\t{os.path.getmtime(db.songPath / song)}')
                        os.remove(pl.location / song)
                        os.link(db.songPath / song, pl.location / song)
                db.save()
                pl.save()

            if action == "download" and dl_window is None:
                songs, _ = pl.results
                needed = [song for song in songs if song not in db.files]
                if len(needed) > 0:
                    dl_window = open_dl_window(config.jobs)
                    dl_pl = pl
                    for song in needed:
                        dl_queue.put(song)
                    for i in range(config.jobs):
                        fut = tp.submit(db.multi_fetch, dl_window[f'DL-{i}'], dl_window[f'PROG-{i}'], dl_queue)
                        futures.append(fut)
        else:
            ctx.handle_event(window, action, values)
        # endregion Playlist Events

    exit()


if __name__ == "__main__":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
