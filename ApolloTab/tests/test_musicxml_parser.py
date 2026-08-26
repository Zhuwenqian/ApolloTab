import zipfile

from ApolloTab import parse_musicxml, parse_score, render_musicxml


SCORE = '''<?xml version="1.0"?>
<score-partwise version="4.0"><work><work-title>Importer Test</work-title></work>
<part-list><score-part id="P1"><part-name>Guitar</part-name><midi-instrument id="P1-I1"><midi-program>31</midi-program></midi-instrument></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>2</divisions><key><fifths>1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><staff-details><staff-tuning line="1"><tuning-step>E</tuning-step><tuning-octave>2</tuning-octave></staff-tuning><staff-tuning line="6"><tuning-step>E</tuning-step><tuning-octave>4</tuning-octave></staff-tuning></staff-details></attributes><direction><sound tempo="96"/></direction><note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type><notations><technical><string>1</string><fret>0</fret></technical></notations></note><note><chord/><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type><notations><technical><string>2</string><fret>1</fret></technical></notations></note><note><rest/><duration>2</duration><type>quarter</type></note></measure></part></score-partwise>'''


def test_musicxml_parses_chords_metadata_and_dispatch(tmp_path, qapp):
    path = tmp_path / 'score.musicxml'; path.write_text(SCORE, encoding='utf-8')
    song = parse_score(str(path))
    assert song.title == 'Importer Test' and song.tempo == 96
    assert song.tracks[0].name == 'Guitar'
    measure = song.tracks[0].measures[0]
    assert measure.time_signature == (4, 4) and measure.key_signature == 1
    assert [(n.string, n.fret) for n in measure.beats[0].notes] == [(0, 0), (1, 1)]
    assert measure.beats[1].is_rest
    assert len(render_musicxml(str(path))) == 1


def test_compressed_mxl_uses_container_rootfile(tmp_path):
    path = tmp_path / 'score.mxl'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr('score.xml', SCORE)
    assert parse_musicxml(str(path)).track_count == 1


def test_timewise_musicxml_is_normalized_to_tracks(tmp_path):
    xml = '''<score-timewise><part-list><score-part id="P1"><part-name>Bass</part-name></score-part></part-list>
    <measure number="1"><part id="P1"><attributes><divisions>1</divisions></attributes><note><pitch><step>E</step><octave>2</octave></pitch><duration>1</duration><type>quarter</type></note></part></measure></score-timewise>'''
    path = tmp_path / 'timewise.xml'; path.write_text(xml, encoding='utf-8')
    song = parse_musicxml(str(path))
    assert song.tracks[0].name == 'Bass'
    assert song.tracks[0].measures[0].beats[0].notes[0].midi_pitch == 40
