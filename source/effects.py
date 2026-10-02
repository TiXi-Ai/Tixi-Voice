# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional voice effects applied to synthesized speech (numpy + soxr only).

All processing is local and deterministic. Each effect reads the generated WAV,
applies a DSP transform and rewrites the file in place with PCM_16.
"""
from __future__ import annotations

import numpy as np
import soxr
import soundfile as sf

from core import UserError, check_cancel


def _check(cancel):
    """Cancel hook that tolerates a None (used when running standalone/tests)."""
    if cancel is not None:
        check_cancel(cancel)


EFFECTS = {
    'none':        ('بدون افکت', 'خروجی اصلی موتور، بدون تغییر'),
    'robot':       ('ربات', 'صدای متالیک رباتیک با مدولاسیون'),
    'child_girl':  ('دختر بچه', 'زیروبمی بالاتر با رنگ صدای کودکانه'),
    'child_boy':   ('پسر بچه', 'زیروبمی کمی بالاتر و نرم'),
    'chipmunk':    ('سنجاب کارتونی', 'زیروبمی خیلی بالا'),
    'deep':        ('عمیق و بم', 'زیروبمی پایین‌تر و گرم‌تر'),
    'echo':        ('اکو / دور', 'طنین و پژواک'),
    'telephone':   ('تلفنی', 'باند باریک با اعوجاج خفیف'),
}

_PITCH = {
    'child_girl': 1.45,
    'child_boy':  1.22,
    'chipmunk':   1.75,
    'deep':       0.78,
}


def _normalize(x, target=0.95):
    peak = float(np.max(np.abs(x)))
    if peak < 1e-6:
        return x
    x = x * (target / peak)
    return np.clip(x, -0.98, 0.98)


def _pitch_shift(data, sr, factor, cancel):
    """Time-domain pitch shift via resampling; moves pitch and formants together."""
    if abs(factor - 1.0) < 0.001:
        return data
    # Resample so the buffer plays faster (higher pitch), then restore the length.
    target = max(8000, int(round(sr * factor)))
    shifted = soxr.resample(np.asarray(data, dtype='float32'), sr, target, quality='HQ')
    _check(cancel)
    # Stretch back to the original duration.
    back = soxr.resample(shifted, target, sr, quality='HQ')
    if len(back) < len(data):
        back = np.pad(back, (0, len(data) - len(back)))
    return back[:len(data)].astype('float32')


def _bandpass(data, sr, low, high, cancel):
    """Simple bandpass via FFT filtering (causal, phase-neutral)."""
    n = len(data)
    if n < 64:
        return data
    fft = np.fft.rfft(np.asarray(data, dtype='float64'))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    mask = np.ones_like(freqs, dtype='float64')
    # Soft roll-off edges.
    roll = 0.5 * max(1.0, high - low)
    mask *= 1.0 / (1.0 + np.exp(-(freqs - low) / (roll * 0.25)))
    mask *= 1.0 / (1.0 + np.exp((freqs - high) / (roll * 0.25)))
    fft *= mask
    out = np.fft.irfft(fft, n=n).astype('float32')
    _check(cancel)
    return out


def _robot(data, sr, cancel):
    """Ring modulation + harmonic stacking + bitcrush for a metallic robotic tone."""
    n = len(data)
    t = np.arange(n, dtype='float64') / sr
    carrier = np.sin(2 * np.pi * 32.0 * t) * 0.6 + np.sin(2 * np.pi * 63.0 * t) * 0.4
    carrier = (carrier > 0).astype('float64') * 2 - 1  # square wave for harsher ring
    out = np.asarray(data, dtype='float64') * (0.55 + 0.45 * carrier)
    # Add a faint fixed high harmonic for "voice chip" character.
    env = np.abs(np.asarray(data, dtype='float64'))
    env = env / (np.max(env) + 1e-6)
    out = out + 0.12 * np.sin(2 * np.pi * 440.0 * t) * env
    # Bitcrush.
    steps = 2 ** 5
    out = np.round(out * steps) / steps
    _check(cancel)
    return out.astype('float32')


def _echo(data, sr, cancel):
    delay = int(sr * 0.28)
    alpha = 0.42
    out = np.copy(np.asarray(data, dtype='float64'))
    if delay < len(out):
        out[delay:] += alpha * out[:-delay]
    _check(cancel)
    return out.astype('float32')


def _telephone(data, sr, cancel):
    out = _bandpass(data, sr, 300, 3400, cancel)
    # Mild saturation for that handset crunch.
    out = np.tanh(1.7 * out).astype('float32')
    return out


def _apply(kind, data, sr, cancel):
    if kind == 'none':
        return data
    if kind in _PITCH:
        return _pitch_shift(data, sr, _PITCH[kind], cancel)
    if kind == 'robot':
        return _robot(data, sr, cancel)
    if kind == 'echo':
        return _echo(data, sr, cancel)
    if kind == 'telephone':
        return _telephone(data, sr, cancel)
    raise UserError('افکت صوتی انتخاب‌شده معتبر نیست.')


def apply_effect(path, kind, cancel=None, progress=None):
    """Apply a named effect to the WAV at *path* in place."""
    if kind == 'none':
        return path
    if kind not in EFFECTS:
        raise UserError('افکت صوتی انتخاب‌شده معتبر نیست.')
    try:
        data, sr = sf.read(str(path), dtype='float32')
    except Exception as e:
        raise UserError('فایل صوتی خروجی برای اعمال افکت خوانده نشد.') from e
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype('float32')
    if len(data) == 0 or sr <= 0:
        raise UserError('خروجی برای اعمال افکت خالی است.')
    if progress:
        progress(-1, 'اعمال افکت صوتی…')
    processed = _apply(kind, data, sr, cancel)
    processed = _normalize(processed)
    _check(cancel)
    try:
        sf.write(str(path), processed.astype('float32'), sr, subtype='PCM_16')
    except Exception as e:
        raise UserError('افکت اعمال شد اما ذخیرهٔ فایل کامل نشد؛ فضای خالی را بررسی کن.') from e
    if progress:
        progress(-1, 'افکت صوتی اعمال شد.')
    return path
