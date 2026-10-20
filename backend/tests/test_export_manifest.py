"""导出使用创建时的内容，后续剪辑、删除音轨和替换文件不会改变该次导出。"""
import json

import pytest

from app import storyboard as sb, tracks


def test_export_freezes_versions_trims_audio_and_subtitles(fresh_db, tmp_path, monkeypatch):
    import app.config as config
    import app.export as exporter
    import app.video as video
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(config, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'EXPORTS_DIR', out / 'exports')
    project = sb.create_project('frozen export')
    scene = sb.create_scene(project['id'], 'scene')
    shot = sb.create_shot(scene['id'], prompt='test')
    (out / 'clip.mp4').write_bytes(b'original-video')
    (out / 'voice.mp3').write_bytes(b'original-voice')
    version = sb.create_version(shot['id'], {'id': 'source', 'engine': 'anime'}, '/outputs/clip.mp4')
    sb.update_shot(shot['id'], current_version_id=version['id'], status='approved')
    sb.sync_timeline(project['id'])
    item = sb.list_timeline(project['id'])[0]
    sb.update_timeline_item(item['id'], trim_in_s=0.5)
    voice = tracks.create_voice(shot['id'], audio_url='/outputs/voice.mp3', text='original voice')
    tracks.replace_subtitles(project['id'], [{'text': 'original subtitle', 'start_s': 0, 'end_s': 2}])
    exp = sb.create_export(project['id'], fps=30)
    manifest = json.loads(exp['manifest'])

    sb.update_timeline_item(item['id'], trim_in_s=2)
    tracks.delete_voice(voice['id'])
    tracks.replace_subtitles(project['id'], [{'text': 'later subtitle', 'start_s': 0, 'end_s': 1}])
    (out / 'clip.mp4').write_bytes(b'replaced-video')
    assert manifest['items'][0]['current_version_id'] == version['id']
    assert manifest['items'][0]['trim_in_s'] == 0.5
    assert manifest['subtitles'][0]['text'] == 'original subtitle'
    frozen_audio = out / manifest['voices'][0]['audio_url'].removeprefix('/outputs/')
    assert frozen_audio.read_bytes() == b'original-voice'

    monkeypatch.setattr(sb, 'timeline_layout', lambda pid: pytest.fail('must not read live timeline'))
    monkeypatch.setattr(video, 'probe_duration', lambda path: 4.0)
    monkeypatch.setattr(video, 'probe_resolution', lambda path: (512, 512))
    captured = {}

    def concatenate(items, target, **kwargs):
        captured['video'] = open(items[0]['path'], 'rb').read()
        captured['trim'] = items[0]['trim_in_s']
        target.write_bytes(b'joined')

    def mix(pid, layout, target, frozen):
        assert frozen['voices'][0]['text'] == 'original voice'
        return False

    def mux(source, audio, subtitles, target, **kwargs):
        captured['subtitles'] = subtitles.read_text(encoding='utf-8')
        target.write_bytes(b'final')

    monkeypatch.setattr(exporter, 'concat_videos', concatenate)
    monkeypatch.setattr(exporter, '_mix_audio', mix)
    monkeypatch.setattr(exporter, '_final_mux', mux)
    exporter.run_export(exp['id'])
    assert sb.get_export(exp['id'])['status'] == 'done'
    assert captured['video'] == b'original-video'
    assert captured['trim'] == 0.5
    assert 'original subtitle' in captured['subtitles']
    assert 'later subtitle' not in captured['subtitles']


def test_missing_media_does_not_create_export(fresh_db):
    project = sb.create_project('missing media')
    scene = sb.create_scene(project['id'], 'scene')
    shot = sb.create_shot(scene['id'], prompt='test')
    version = sb.create_version(shot['id'], {'id': 'missing'}, '/outputs/no-such-video.mp4')
    sb.update_shot(shot['id'], current_version_id=version['id'], status='approved')
    sb.sync_timeline(project['id'])
    with pytest.raises(ValueError, match='素材缺失'):
        sb.create_export(project['id'])
    assert sb.list_exports(project['id']) == []
