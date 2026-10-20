"""使用打包 FFmpeg 验证冻结清单到实际带声音成片的完整链路。"""
import subprocess
import math
import struct

import pytest

from app import storyboard as sb, tracks
from app.video import get_ffmpeg, probe_duration


def test_real_export_survives_original_media_changes(fresh_db, tmp_path, monkeypatch):
    import app.config as config
    import app.export as exporter
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(config, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'EXPORTS_DIR', out / 'exports')
    clip = out / 'clip.mp4'
    voice = out / 'voice.wav'
    created = subprocess.run([
        get_ffmpeg(), '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=1.5',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5',
        '-map', '0:v', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip),
        '-map', '1:a', '-c:a', 'pcm_s16le', str(voice),
    ], capture_output=True)
    assert created.returncode == 0, created.stderr.decode('utf-8', errors='replace')
    project = sb.create_project('actual export')
    scene = sb.create_scene(project['id'], 'scene')
    shot = sb.create_shot(scene['id'], prompt='test')
    version = sb.create_version(shot['id'], {'id': 'source'}, '/outputs/clip.mp4')
    sb.update_shot(shot['id'], current_version_id=version['id'], status='approved')
    sb.sync_timeline(project['id'])
    item = sb.list_timeline(project['id'])[0]
    sb.update_timeline_item(item['id'], trim_in_s=0.25, trim_out_s=1.25)
    tracks.create_voice(shot['id'], audio_url='/outputs/voice.wav', text='test voice')
    tracks.replace_subtitles(project['id'], [{'text': 'Frozen subtitle', 'start_s': 0, 'end_s': 1}])
    export = sb.create_export(project['id'], fps=30)
    clip.write_bytes(b'later replacement')
    voice.unlink()
    tracks.replace_subtitles(project['id'], [])
    exporter.run_export(export['id'])
    result = sb.get_export(export['id'])
    assert result['status'] == 'done', result['error']
    final = out / result['file_url'].removeprefix('/outputs/')
    assert 0.8 <= probe_duration(final) <= 1.2
    decoded = subprocess.run([get_ffmpeg(), '-i', str(final), '-f', 'null', '-'], capture_output=True)
    assert decoded.returncode == 0, decoded.stderr.decode('utf-8', errors='replace')
    description = decoded.stderr.decode('utf-8', errors='replace')
    assert 'Audio:' in description
    assert '30 fps' in description


@pytest.fixture
def media_env(fresh_db, tmp_path, monkeypatch):
    import app.config as config
    import app.export as exporter
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(config, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(exporter, 'EXPORTS_DIR', out / 'exports')
    return out, exporter


def run_ffmpeg(*args):
    result = subprocess.run([get_ffmpeg(), '-y', *map(str, args)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')


def add_clip(project, filename):
    scene = sb.create_scene(project['id'], filename)
    shot = sb.create_shot(scene['id'], prompt='test')
    version = sb.create_version(shot['id'], {'id': filename}, '/outputs/' + filename)
    sb.update_shot(shot['id'], current_version_id=version['id'], status='approved')
    return shot


def export_project(project, out, exporter):
    exp = sb.create_export(project['id'])
    exporter.run_export(exp['id'])
    result = sb.get_export(exp['id'])
    assert result['status'] == 'done', result['error']
    return out / result['file_url'].removeprefix('/outputs/')


def samples(path, start, length):
    result = subprocess.run([
        get_ffmpeg(), '-ss', str(start), '-i', str(path), '-t', str(length),
        '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-',
    ], capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    return [s[0] for s in struct.iter_unpack('<f', result.stdout)]


def tone_strength(values, frequency):
    # 频率相关幅度能区分真正保留原声、错取裁剪区间和仅输出静音/其他混音。
    return abs(sum(v * complex(math.cos(2 * math.pi * frequency * i / 8000),
                               math.sin(2 * math.pi * frequency * i / 8000))
                   for i, v in enumerate(values))) / len(values)


def test_native_audio_survives_trim_and_silent_clip_then_mixes_with_voice_and_music(media_env):
    out, exporter = media_env
    run_ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=1',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', out / 'silent.mp4')
    run_ffmpeg('-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=24:d=2',
               '-f', 'lavfi', '-i',
               r'aevalsrc=if(lt(t\,1)\,0.2*sin(2*PI*440*t)\,0.2*sin(2*PI*880*t)):s=44100:d=2',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', out / 'native.mp4')
    project = sb.create_project('Native audio regression')
    add_clip(project, 'silent.mp4')
    shot = add_clip(project, 'native.mp4')
    sb.sync_timeline(project['id'])
    item = next(it for it in sb.list_timeline(project['id']) if it['shot_id'] == shot['id'])
    sb.update_timeline_item(item['id'], trim_in_s=1, trim_out_s=2)
    # 先验原声独立保留及定位：第一段静音，第二段取源视频第二秒（880 Hz）。
    native_final = export_project(project, out, exporter)
    quiet = samples(native_final, 0.2, 0.5)
    audible = samples(native_final, 1.2, 0.5)
    assert max(map(abs, quiet)) < 0.005
    assert tone_strength(audible, 880) > 0.04
    assert tone_strength(audible, 440) < 0.005
    assert 1.95 <= probe_duration(native_final) <= 2.1
    # 原声还要能与独立配音、背景音乐同时保留。
    for name, frequency in [('voice', 220), ('music', 110)]:
        run_ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration=3', out / f'{name}.wav')
    tracks.create_voice(shot['id'], audio_url='/outputs/voice.wav')
    tracks.create_audio_track(project['id'], audio_url='/outputs/music.wav', volume=0.5)
    mixed_final = export_project(project, out, exporter)
    mixed = samples(mixed_final, 1.2, 0.5)
    assert all(tone_strength(mixed, freq) > 0.01 for freq in (880, 220, 110))
    assert 1.95 <= probe_duration(mixed_final) <= 2.1


@pytest.mark.parametrize('music_duration', [0.2, 3.0])
def test_music_neither_shortens_nor_extends_video(media_env, music_duration):
    out, exporter = media_env
    run_ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24:d=1',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', out / 'clip.mp4')
    run_ffmpeg('-f', 'lavfi', '-i', f'sine=frequency=440:duration={music_duration}', out / 'music.wav')
    project = sb.create_project('Music duration regression')
    add_clip(project, 'clip.mp4')
    sb.sync_timeline(project['id'])
    tracks.create_audio_track(project['id'], audio_url='/outputs/music.wav')
    # 超时字幕也不能拉长成片（烧录及软字幕路径共享输出边界）。
    tracks.replace_subtitles(project['id'], [{'text': 'test', 'start_s': 0, 'end_s': 4}])
    final = export_project(project, out, exporter)
    assert 0.95 <= probe_duration(final) <= 1.1
    first = samples(final, 0.05, 0.1)
    assert tone_strength(first, 440) > 0.01
    if music_duration < 1:
        assert max(map(abs, samples(final, 0.5, 0.3))) < 0.005


def test_audio_trim_out_is_source_position_not_extra_duration(media_env):
    out, exporter = media_env
    run_ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', out / 'music.wav')
    project = sb.create_project('Audio trim regression')
    tracks.create_audio_track(project['id'], audio_url='/outputs/music.wav',
                              start_s=0.5, trim_in_s=1, trim_out_s=1.5)
    mixed = out / 'mix.wav'
    assert exporter._mix_audio(project['id'], [{'shot_id': '', 'start_s': 0, 'end_s': 2}], mixed)
    assert max(map(abs, samples(mixed, 0.05, 0.3))) < 0.005
    assert tone_strength(samples(mixed, 0.6, 0.2), 440) > 0.01
    assert max(map(abs, samples(mixed, 1.1, 0.5))) < 0.005
    assert 1.99 <= probe_duration(mixed) <= 2.01
