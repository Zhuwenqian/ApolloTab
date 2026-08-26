"""MusicXML importer which normalizes scores into ApolloTab's GTPSong model."""

from __future__ import annotations

import contextlib
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..models.beat import GTPBeat
from ..models.measure import GTPMeasure
from ..models.note import GTPNote
from ..models.song import GTPSong
from ..models.track import GTPTrack
from ..utils.constants import NoteDuration, TechniqueType


def _name(e: ET.Element) -> str:
    return e.tag.rsplit('}', 1)[-1]


def _kids(e: ET.Element | None, tag: str) -> list[ET.Element]:
    return [] if e is None else [x for x in e if _name(x) == tag]


def _kid(e: ET.Element | None, tag: str) -> ET.Element | None:
    return next(iter(_kids(e, tag)), None)


def _text(e: ET.Element | None) -> str:
    return (e.text or '').strip() if e is not None else ''


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (ValueError, TypeError):
        return default


class MusicXmlParser:
    """Read MusicXML 3.x/4.x, including compressed ``.mxl`` archives.

    As in alphaTab, the format importer is kept independent from rendering.  The
    existing :class:`TabRenderer` therefore renders the returned song unchanged.
    """

    TYPES = {
        'whole': NoteDuration.WHOLE,
        'half': NoteDuration.HALF,
        'quarter': NoteDuration.QUARTER,
        'eighth': NoteDuration.EIGHTH,
        '16th': NoteDuration.SIXTEENTH,
        '32nd': NoteDuration.THIRTY_SECOND,
    }
    STEPS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    RATIOS = {
        NoteDuration.WHOLE: 4,
        NoteDuration.HALF: 2,
        NoteDuration.QUARTER: 1,
        NoteDuration.EIGHTH: 0.5,
        NoteDuration.SIXTEENTH: 0.25,
        NoteDuration.THIRTY_SECOND: 0.125,
    }

    def parse_file(self, file_path: str) -> GTPSong:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(file_path)
        try:
            root = ET.fromstring(self._read(path))
        except ET.ParseError as exc:
            raise ValueError(f'Invalid MusicXML: {exc}') from exc
        if _name(root) not in ('score-partwise', 'score-timewise'):
            raise ValueError(f'Unsupported XML root <{_name(root)}>; expected a MusicXML score')
        return self._parse(root)

    def _read(self, path: Path) -> bytes:
        if path.suffix.lower() != '.mxl':
            return path.read_bytes()
        try:
            with zipfile.ZipFile(path) as z:
                member = None
                if 'META-INF/container.xml' in z.namelist():
                    container = ET.fromstring(z.read('META-INF/container.xml'))
                    rootfile = next((x for x in container.iter() if _name(x) == 'rootfile'), None)
                    member = rootfile.get('full-path') if rootfile is not None else None
                member = member or next(
                    (x for x in z.namelist() if x.lower().endswith(('.xml', '.musicxml'))), None
                )
                if not member:
                    raise ValueError('Compressed MusicXML contains no score XML')
                return z.read(member)
        except zipfile.BadZipFile as exc:
            raise ValueError('Invalid compressed MusicXML archive') from exc

    def _parse(self, root: ET.Element) -> GTPSong:
        song = GTPSong()
        work, movement = _kid(root, 'work'), _kid(root, 'movement-title')
        song.title = _text(_kid(work, 'work-title')) or _text(movement)
        ident = _kid(root, 'identification')
        for c in _kids(ident, 'creator'):
            if c.get('type') == 'composer':
                song.music = _text(c)
            elif c.get('type') == 'lyricist':
                song.words = _text(c)
            elif c.get('type') in ('artist', 'arranger'):
                song.artist = _text(c)
        song.artist = song.artist or song.music
        song.copyright = _text(_kid(ident, 'rights'))
        names, programs = self._part_list(root)
        for number, (part_id, measures) in enumerate(self._parts(root), 1):
            track = GTPTrack(
                name=names.get(part_id, part_id or f'Part {number}'),
                number=number,
                instrument=programs.get(part_id, 30),
            )
            self._part(measures, track, song)
            song.tracks.append(track)
        if not song.tracks:
            raise ValueError('MusicXML score contains no parts')
        return song

    def _part_list(self, root: ET.Element) -> tuple[dict[str, str], dict[str, int]]:
        names, programs = {}, {}
        for p in _kids(_kid(root, 'part-list'), 'score-part'):
            ident = p.get('id', '')
            names[ident] = _text(_kid(p, 'part-name')) or ident
            midi = _kid(p, 'midi-instrument')
            programs[ident] = max(0, min(127, _int(_text(_kid(midi, 'midi-program')), 31) - 1))
        return names, programs

    def _parts(self, root: ET.Element) -> list[tuple[str, list[ET.Element]]]:
        if _name(root) == 'score-partwise':
            return [(p.get('id', ''), _kids(p, 'measure')) for p in _kids(root, 'part')]
        result = defaultdict(list)
        for m in _kids(root, 'measure'):
            for p in _kids(m, 'part'):
                copy = ET.Element('measure', m.attrib)
                copy.extend(list(p))
                result[p.get('id', '')].append(copy)
        return list(result.items())

    def _part(self, elements: list[ET.Element], track: GTPTrack, song: GTPSong) -> None:
        state: dict[str, Any] = {'divisions': 1, 'time': (4, 4), 'key': 0}
        for index, e in enumerate(elements, 1):
            track.measures.append(self._measure(e, index, track, song, state))

    def _measure(
        self, e: ET.Element, number: int, track: GTPTrack, song: GTPSong, state: dict[str, Any]
    ) -> GTPMeasure:
        result = GTPMeasure(number=number, time_signature=state['time'], key_signature=state['key'])
        cursor = 0
        last_start = 0
        beats: dict[int, GTPBeat] = {}
        for child in e:
            tag = _name(child)
            if tag == 'attributes':
                self._attributes(child, track, state)
                result.time_signature, result.key_signature = state['time'], state['key']
            elif tag == 'backup':
                cursor = max(0, cursor - _int(_text(_kid(child, 'duration'))))
            elif tag == 'forward':
                cursor += _int(_text(_kid(child, 'duration')))
            elif tag == 'direction':
                self._direction(child, song, beats.get(cursor))
            elif tag == 'barline':
                repeat = _kid(child, 'repeat')
                if repeat is not None:
                    if repeat.get('direction') == 'forward':
                        result.is_repeat_open = True
                    if repeat.get('direction') == 'backward':
                        result.repeat_close = _int(repeat.get('times'), 2)
            elif tag == 'note':
                chord = _kid(child, 'chord') is not None
                start = last_start if chord else cursor
                beat = beats.setdefault(start, self._beat(child, state['divisions']))
                self._note(child, beat, track)
                if not chord:
                    last_start, cursor = start, cursor + _int(_text(_kid(child, 'duration')))
        result.beats = [beats[k] for k in sorted(beats)]
        return result

    def _attributes(self, e: ET.Element, track: GTPTrack, state: dict[str, Any]) -> None:
        d = _kid(e, 'divisions')
        if d is not None:
            state['divisions'] = max(1, _int(_text(d), 1))
        time, key = _kid(e, 'time'), _kid(e, 'key')
        if time is not None:
            state['time'] = (
                _int(_text(_kid(time, 'beats')), 4),
                _int(_text(_kid(time, 'beat-type')), 4),
            )
        if key is not None:
            state['key'] = _int(_text(_kid(key, 'fifths')))
        tunings = []
        for details in _kids(e, 'staff-details'):
            for tuning in _kids(details, 'staff-tuning'):
                step = _text(_kid(tuning, 'tuning-step'))
                octave = _int(_text(_kid(tuning, 'tuning-octave')), 4)
                if step in self.STEPS:
                    tunings.append(
                        (octave + 1) * 12
                        + self.STEPS[step]
                        + _int(_text(_kid(tuning, 'tuning-alter')))
                    )
        if tunings:
            track.strings = tuple(reversed(tunings))

    def _beat(self, e: ET.Element, divisions: int) -> GTPBeat:
        duration = _int(_text(_kid(e, 'duration')))
        kind = _text(_kid(e, 'type'))
        value = self.TYPES.get(kind) or min(
            self.RATIOS, key=lambda x: abs(self.RATIOS[x] - duration / max(1, divisions))
        )
        beat = GTPBeat(
            duration=value,
            is_dotted=_kid(e, 'dot') is not None,
            is_rest=_kid(e, 'rest') is not None,
        )
        mod = _kid(e, 'time-modification')
        actual, normal = (
            _int(_text(_kid(mod, 'actual-notes')), -1),
            _int(_text(_kid(mod, 'normal-notes')), -1),
        )
        if actual > 0 and normal > 0:
            beat.tuplet_numerator, beat.tuplet_denominator = actual, normal
        lyric = _kid(e, 'lyric')
        text = _text(_kid(lyric, 'text'))
        if text:
            beat.lyrics = [text]
        return beat

    def _note(self, e: ET.Element, beat: GTPBeat, track: GTPTrack) -> None:
        if _kid(e, 'rest') is not None:
            return
        pitch = _kid(e, 'pitch')
        if pitch is None:
            pitch = _kid(e, 'unpitched')
        step = _text(_kid(pitch, 'step'))
        if step not in self.STEPS:
            return
        midi = max(
            0,
            min(
                127,
                (_int(_text(_kid(pitch, 'octave')), 4) + 1) * 12
                + self.STEPS[step]
                + _int(_text(_kid(pitch, 'alter'))),
            ),
        )
        tech = _kid(_kid(e, 'notations'), 'technical')
        string, fret = _int(_text(_kid(tech, 'string'))) - 1, _int(_text(_kid(tech, 'fret')), -1)
        used = {n.string for n in beat.notes}
        if not (0 <= string < track.string_count and fret >= 0):
            choices = [
                (midi - base, i)
                for i, base in enumerate(track.strings)
                if 0 <= midi - base <= track.fret_count and i not in used
            ]
            fret, string = min(choices, default=(max(0, midi - track.strings[0]), 0))
        note = GTPNote(
            midi_pitch=midi,
            string=string,
            fret=fret,
            duration=beat.duration,
            is_dotted=beat.is_dotted,
        )
        note.is_tie_destination = any(x.get('type') == 'stop' for x in _kids(e, 'tie'))
        notations, articulation = _kid(e, 'notations'), None
        if notations is not None:
            articulation = _kid(notations, 'articulations')
            tech = _kid(notations, 'technical')
            if articulation is not None:
                note.is_staccato, note.is_tenuto = (
                    _kid(articulation, 'staccato') is not None,
                    _kid(articulation, 'tenuto') is not None,
                )
                if _kid(articulation, 'accent') is not None:
                    note.techniques.append(TechniqueType.ACCENTUATED)
            for tag, value in {
                'hammer-on': TechniqueType.HAMMER_ON,
                'pull-off': TechniqueType.PULL_OFF,
            }.items():
                if _kid(tech, tag) is not None:
                    note.techniques.append(value)
            if _kid(tech, 'harmonic') is not None:
                note.techniques.append(TechniqueType.NATURAL_HARMONIC)
            if _kid(_kid(notations, 'ornaments'), 'trill-mark') is not None:
                note.techniques.append(TechniqueType.TRILL)
        beat.notes.append(note)
        beat.is_rest = False

    @staticmethod
    def _direction(e: ET.Element, song: GTPSong, beat: GTPBeat | None) -> None:
        sound = _kid(e, 'sound')
        tempo = sound.get('tempo') if sound is not None else None
        if tempo:
            with contextlib.suppress(ValueError):
                song.tempo = round(float(tempo))
        words = _kid(_kid(e, 'direction-type'), 'words')
        if beat is not None and words is not None:
            beat.text = _text(words)


def parse_musicxml(file_path: str) -> GTPSong:
    """Parse a MusicXML ``.musicxml``/``.xml`` score or compressed ``.mxl``."""
    return MusicXmlParser().parse_file(file_path)
