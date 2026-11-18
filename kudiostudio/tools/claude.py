# -*- coding: utf-8 -*-
"""Asking Claude what a recording's measurements say.

Claude takes text and images, not audio, so nothing of the recording itself
goes: kudio measures it here and the figures go, the ones the Claude panel
lists before it sends them. The answer comes back in a fixed shape (a
summary, findings, next steps naming this window's panels) and is read
back as JSON.

The key is the user's own. It is kept in the system's credential store
(Windows Credential Manager, the macOS keychain), never in the settings
file; with none saved there, the SDK looks where it always does, the
ANTHROPIC_API_KEY variable or an ``ant auth login`` profile.

Models change, so little here is pinned to one. "auto" is the model this
release recommends, the list the Settings dialog offers comes from the
Models API, a parameter goes only to a model that says it takes it, and a
model that has been retired falls back to the recommended one.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: what "auto" means in this release. Changed here when a better model for
#: the job is out and has been tried on it.
RECOMMENDED_MODEL = "claude-sonnet-5-5"
#: its name, for before the Models API has been asked for any
RECOMMENDED_NAME = "Claude Sonnet 5.5"
#: models that take ``fallbacks: "default"``, which re-runs an answer their
#: safety classifiers declined on the model Anthropic recommends for it.
#: Another model goes without, rather than be sent a parameter it may refuse
FALLBACK_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: US dollars per million tokens, in and out, for the cost line under an
#: answer. A model not listed shows its token counts alone
PRICES: Dict[str, Tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-2": (8.0, 40.0),
}
#: where a key is made
CONSOLE_URL = "https://platform.claude.com/settings/keys"
#: the credential store's entry for the key
KEYRING_SERVICE = "KudioStudio"
KEYRING_USER = "anthropic-api-key"
#: an answer is a few hundred words; this leaves room for the thinking that
#: comes before it
MAX_TOKENS = 16000

#: the panels a next step may name, as their ids
PANELS = ("inspect", "effects", "enhance", "compare", "record", "synthesize",
          "manifest", "pitch", "metrics", "batch")

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["ok", "minor", "major"]},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "title", "detail"],
                "additionalProperties": False,
            },
        },
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel": {"type": "string", "enum": [*PANELS, "none"]},
                    "action": {"type": "string"},
                },
                "required": ["panel", "action"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "string"},
    },
    "required": ["summary", "findings", "next_steps", "caveats"],
    "additionalProperties": False,
}

SYSTEM = """\
You read the measurements of one audio recording for someone working on it \
in KudioStudio, a desktop audio workbench built on the kudio library. You get \
numbers only, never the audio, so say what the numbers show and what they \
cannot: they do not tell you what is said, who says it, or how it sounds to \
a listener.

Write for a person who records and edits speech, not for an engineer. Give \
figures where they carry the point (a peak of -0.1 dBFS, 3 clipped samples) \
and leave out the ones that do not. Do not invent a measurement that is not \
in the input, and when two figures disagree or a figure is missing, say so \
in caveats.

findings: what is wrong, or that a figure is fine, most important first. \
major is a fault that will be heard or that breaks later processing \
(clipping, a non-finite sample, almost no signal over the noise); minor is \
worth knowing (a level well off a delivery target, a narrow bandwidth that \
looks like a phone line or a resampled file, a DC offset); ok is a figure \
that is fine and worth saying so.

next_steps: concrete actions in KudioStudio, each naming the panel that does \
it, with settings when the figures support them. The panels are:
- inspect: analysis view, levels, selection
- effects: gain, fades, remove_dc, highpass, lowpass, bandpass, bandstop, \
pitch_shift, time_stretch, reverb and room simulation, add_noise_snr, \
telephone, bit_depth, dropouts, trim_silence, vad_trim, split_on_silence
- enhance: denoising, with kudio's statistical denoisers (logmmse, Wiener, \
spectral subtraction and others), wavelet filtering, or a learned model
- compare: runs every denoiser on the same audio and scores them in one table
- record: records a new take
- synthesize: mixes clean speech with noise for training data
- manifest: reads a synthesised corpus's manifest
- pitch: tracks the fundamental frequency
- metrics: quality measures without a reference (SRMR, WADA-SNR, DNSMOS) \
and against a clean reference (PESQ, STOI, SI-SDR and others)
- batch: the same conversion, levelling or denoising over a whole folder
Use "none" for a step outside KudioStudio, such as recording again with \
the microphone further from the mouth. There is no declipping tool: clipped \
audio can only be avoided by recording again.

Write every string in {language}."""

#: the language an answer is written in, by the window's language code
LANGUAGES = {"en": "English", "zh_TW": "Traditional Chinese as used in Taiwan"}


class ClaudeError(Exception):
    """A request that did not give an answer, in words for the status line.
    *retired* marks a model the API no longer has."""

    def __init__(self, message: str, retired: bool = False) -> None:
        super().__init__(message)
        self.retired = retired


@dataclass
class Answer:
    summary: str
    findings: List[Dict[str, str]]
    next_steps: List[Dict[str, str]]
    caveats: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: US dollars, or None for a model without a price here
    cost: Optional[float] = None
    #: the model that answered when a declined answer was re-run on another
    answered_by: str = ""
    sent: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {"summary": self.summary, "findings": self.findings,
                "next_steps": self.next_steps, "caveats": self.caveats,
                "model": self.model, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost": self.cost,
                "answered_by": self.answered_by}


# ----------------------------------------------------------- what is there

def missing() -> str:
    """Why the Claude panel cannot work here, or "" when it can: KudioStudio
    installs the SDK, but a copy set up with --no-deps has none."""
    if importlib.util.find_spec("anthropic") is None:
        return 'Asking Claude needs the anthropic package: pip install "anthropic>=2,<3" keyring'
    return ""


class KeyStore:
    """The key in the system's credential store. Every failure of the store
    (no backend on a bare Linux box, a locked keychain) reads as no key.

    Read once and then kept for the run: the panel asks where the key comes
    from every time it shows its state, and the macOS keychain can put up a
    question for each read by a program it does not know."""

    def __init__(self) -> None:
        self._known: Optional[str] = None

    def _keyring(self):
        try:
            import keyring
            return keyring
        except Exception:  # noqa: BLE001
            return None

    def get(self) -> str:
        if self._known is not None:
            return self._known
        keyring = self._keyring()
        if keyring is None:
            self._known = ""
            return ""
        try:
            self._known = keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
        except Exception as e:  # noqa: BLE001
            log.info("credential store not readable: %s", e)
            self._known = ""
        return self._known

    def set(self, key: str) -> bool:
        keyring = self._keyring()
        if keyring is None:
            return False
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        except Exception as e:  # noqa: BLE001
            log.warning("key not saved: %s", e)
            return False
        self._known = key
        return True

    def delete(self) -> None:
        self._known = ""
        keyring = self._keyring()
        if keyring is None:
            return
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception as e:  # noqa: BLE001 - nothing saved, most often
            log.debug("no key removed: %s", e)


def looks_like_a_key(text: str) -> bool:
    """A key as the console hands it out: sk-ant- and then some 40 more
    characters. Checked before it is stored, so a stray paste does not
    become the key."""
    text = text.strip()
    return text.startswith("sk-ant-") and len(text) >= 40 and " " not in text


def key_source(store: KeyStore) -> Tuple[str, str]:
    """Where the key comes from, and its last four characters when it is the
    saved one: ("saved", "…abcd"), ("environment", ""), or ("none", "")."""
    saved = store.get()
    if saved:
        return "saved", "…" + saved[-4:]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "environment", ""
    return "none", ""


def make_client(store: KeyStore):
    """An Anthropic client on the saved key, or on whatever the SDK finds by
    itself: the environment, then an ``ant auth login`` profile."""
    import anthropic
    saved = store.get()
    return anthropic.Anthropic(api_key=saved) if saved else anthropic.Anthropic()


# ------------------------------------------------------------------ models

def resolve(model: str) -> str:
    return RECOMMENDED_MODEL if model in ("", "auto") else model


def list_models(client) -> List[Dict]:
    """The models the key can use that return structured output, newest
    first as the API lists them: id, name, and whether they take effort."""
    found = []
    for m in client.models.list():
        caps = m.capabilities or {}
        try:
            if not caps["structured_outputs"]["supported"]:
                continue
            effort = bool(caps["effort"]["medium"]["supported"])
        except (KeyError, TypeError):
            continue
        found.append({"id": m.id, "name": m.display_name or m.id, "effort": effort,
                      "lifecycle": str(getattr(m, "lifecycle", "") or "")})
    return found


def request(model: str, facts: Dict, language: str,
            known: Sequence[Dict] = ()) -> Dict:
    """The keyword arguments of ``client.beta.messages.create`` for asking
    *model* about *facts*: only what every current model takes, plus effort
    and the refusal fallback where the model is known to take them."""
    info = next((m for m in known if m["id"] == model), None)
    output = {"format": {"type": "json_schema", "schema": SCHEMA}}
    # the figures are few and the reading is not hard; medium keeps an
    # answer to a few seconds
    if model == RECOMMENDED_MODEL or (info and info.get("effort")):
        output["effort"] = "medium"
    kwargs = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM.replace("{language}", LANGUAGES.get(language, "English")),
        "messages": [{"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=1)}],
        "output_config": output,
        "betas": [],
    }
    if model in FALLBACK_MODELS:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


def ask(client, model: str, facts: Dict, language: str,
        known: Sequence[Dict] = ()) -> Answer:
    """Claude's reading of *facts*. Raises ClaudeError with a sentence for
    the status line when there is none."""
    model = resolve(model)
    try:
        response = client.beta.messages.create(**request(model, facts, language, known))
    except Exception as e:  # noqa: BLE001 - every failure is said, not raised
        raise said(e, model) from e
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise ClaudeError(f"Claude declined to answer ({category or 'no reason given'}).")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("The answer ran past its length limit and was cut off.")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ClaudeError("The answer could not be read.") from e
    usage = response.usage
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    answered = str(getattr(response, "model", "") or model)
    price = PRICES.get(answered) or PRICES.get(model)
    cost = None if price is None else (tokens_in * price[0] + tokens_out * price[1]) / 1e6
    return Answer(
        summary=str(data.get("summary", "")),
        findings=[f for f in data.get("findings", []) if isinstance(f, dict)],
        next_steps=[s for s in data.get("next_steps", []) if isinstance(s, dict)],
        caveats=str(data.get("caveats", "")),
        model=model, input_tokens=tokens_in, output_tokens=tokens_out, cost=cost,
        answered_by="" if answered == model else answered, sent=facts)


def said(error: Exception, model: str) -> ClaudeError:
    """*error* from the SDK as one sentence; the most specific class first."""
    import anthropic
    name = type(error).__name__
    if isinstance(error, (anthropic.AuthenticationError, anthropic.CredentialsError)):
        return ClaudeError("The API key was refused, or there is none. Add one in "
                           "Edit → Settings…")
    if isinstance(error, anthropic.PermissionDeniedError):
        return ClaudeError(f"This key may not use {model}.")
    if isinstance(error, anthropic.NotFoundError):
        return ClaudeError(f"{model} is not available any more.", retired=True)
    if isinstance(error, anthropic.PaymentRequiredError):
        return ClaudeError("The account behind this key has no credit left.")
    if isinstance(error, anthropic.RateLimitError):
        wait = error.response.headers.get("retry-after", "") if error.response else ""
        return ClaudeError("Too many requests for now"
                           + (f"; try again in {wait} s." if wait else "; try again shortly."))
    if isinstance(error, anthropic.APIConnectionError):
        return ClaudeError("No connection to the Anthropic API.")
    if isinstance(error, anthropic.APIStatusError):
        return ClaudeError(f"The Anthropic API could not answer (HTTP {error.status_code}); "
                           f"try again later.")
    return ClaudeError(f"Asking Claude failed — {name}: {error}")


# ------------------------------------------------------------------ facts

def _round(value, digits: int = 2):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number or number in (float("inf"), float("-inf")) else round(number, digits)


def facts(report, true_peak_db: float, range_lu: float, channels: int, edited: bool,
          metrics: Sequence[Sequence[str]] = ()) -> Dict:
    """What is sent: kudio's measurements of the clip, and the Metrics
    panel's rows when they were made on the audio as it is now. No file
    name, no path, no samples."""
    levels = {
        "duration_s": _round(report.duration, 3),
        "sample_rate_hz": int(report.sr),
        "channels": int(channels),
        "edited_since_opened": bool(edited),
        "peak_dbfs": _round(report.peak_dbfs),
        "true_peak_dbtp": _round(true_peak_db),
        "rms_dbfs": _round(report.rms_dbfs),
        "loudness_lufs": _round(report.lufs),
        "loudness_range_lu": _round(range_lu),
        "noise_floor_dbfs": _round(report.noise_floor_dbfs),
        "estimated_snr_db": _round(report.estimated_snr_db),
        "bandwidth_hz": _round(report.bandwidth_hz, 0),
        "band_limited": bool(report.band_limited),
        "clipped_samples": int(report.clipped_samples),
        "clipped_ratio": _round(report.clipped_ratio, 6),
        "dc_offset": _round(report.dc_offset, 5),
        "silence_ratio": _round(report.silence_ratio, 3),
        "non_finite_samples": int(report.non_finite_samples),
        "kudio_problems": list(report.problems()),
    }
    out = {"measurements": levels}
    if metrics:
        out["metrics_panel"] = [{"measure": str(a), "value": str(b)} for a, b in metrics]
    return out


__all__ = ["Answer", "CONSOLE_URL", "ClaudeError", "KeyStore", "PANELS", "PRICES",
           "RECOMMENDED_MODEL", "RECOMMENDED_NAME", "SCHEMA", "ask", "facts", "key_source", "list_models",
           "looks_like_a_key", "make_client", "missing", "request", "resolve", "said"]
