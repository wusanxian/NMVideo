"""删除引用不销毁共享素材，包括跨项目与不同轨道类型。"""
import pytest

from app import storyboard as sb, tracks


@pytest.mark.parametrize('first_kind', ['voice', 'music'])
def test_deleting_one_track_preserves_shared_audio(fresh_db, tmp_path, monkeypatch, first_kind):
    import app.config as config
    monkeypatch.setattr(config, 'OUTPUTS_DIR', tmp_path)
    shared = tmp_path / 'shared.wav'
    shared.write_bytes(b'shared audio')
    project = sb.create_project('First')
    other = sb.create_project('Second')
    scene = sb.create_scene(project['id'], 'Scene')
    shot = sb.create_shot(scene['id'])
    voice = tracks.create_voice(shot['id'], audio_url='/outputs/shared.wav')
    music = tracks.create_audio_track(project['id'], audio_url='/outputs/shared.wav')
    remaining = tracks.create_audio_track(other['id'], audio_url='/outputs/shared.wav', kind='sfx')
    if first_kind == 'voice':
        tracks.delete_voice(voice['id'])
        assert tracks.get_voice(voice['id']) is None
    else:
        tracks.delete_audio_track(music['id'])
        assert tracks.list_audio_tracks(project['id']) == []
    assert tracks.list_audio_tracks(other['id'])[0]['id'] == remaining['id']
    assert shared.read_bytes() == b'shared audio'
    tracks.delete_voice(voice['id'])
    tracks.delete_audio_track(music['id'])
    tracks.delete_audio_track(remaining['id'])
    # 最后一个引用删除也保留素材，空间回收须走独立的资产清理流程。
    assert shared.is_file()
