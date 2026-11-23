import wave

import pytest


def test_installed_whisper_decodes_pcm_audio(tmp_path):
    pytest.importorskip("faster_whisper")
    from faster_whisper.audio import decode_audio

    source = tmp_path / "silence.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)

    decoded = decode_audio(str(source))
    assert decoded.shape == (16000,)
    assert decoded.dtype.name == "float32"
    assert not decoded.any()
