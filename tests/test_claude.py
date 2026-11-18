# -*- coding: utf-8 -*-
"""The Claude panel: what is sent, what is asked of which model, what comes
back, and every way it can fail, all against a stand-in client. Nothing
here reaches the API or needs a key."""
from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import kudio
from kudiostudio.tools import claude
from kudiostudio.web.panels.claude import shown_rows
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio

ANSWER = {"summary": "Clean and a little quiet.",
          "findings": [{"severity": "minor", "title": "Quiet", "detail": "-31 LUFS"}],
          "next_steps": [{"panel": "effects", "action": "Raise the gain by 8 dB."},
                         {"panel": "none", "action": "Keep the microphone where it was."}],
          "caveats": "No listening test behind this."}


class FakeClient:
    """The two calls the panel makes, recorded; *replies* are what
    messages.create gives or raises, one per call, the last repeated."""

    def __init__(self, replies=(), models=()):
        self.replies = list(replies) or [ANSWER]
        self.sent = []
        self._models = list(models)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=lambda: iter(self._models))

    def with_options(self, **_):
        return self

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, model=kwargs["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(reply))],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=300))


class MemoryKeys(claude.KeyStore):
    def __init__(self, key=""):
        self.key = key

    def get(self):
        return self.key

    def set(self, key):
        self.key = key
        return True

    def delete(self):
        self.key = ""


def model(id_, name, structured=True, effort=True):
    return SimpleNamespace(id=id_, display_name=name, lifecycle="active", capabilities={
        "structured_outputs": {"supported": structured},
        "effort": {"supported": effort, "medium": {"supported": effort}}})


def _http_error(cls, status, headers=None):
    httpx2 = pytest.importorskip("httpx2")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("from the API", response=httpx2.Response(status, headers=headers or {},
                                                         request=request), body=None)


@pytest.fixture
def studio(tmp_path, monkeypatch, tone):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    # the panel works on any Python with the stand-in client; missing()
    # itself is tested on its own below
    monkeypatch.setattr(claude, "missing", lambda: "")
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.panel("claude").keys = MemoryKeys("sk-ant-api03-" + "x" * 40 + "WXYZ")
    s.call("open_files", [[str(tone)]])
    s.state()
    yield s
    s.shutdown()


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(claude, "make_client", lambda keys: fake)
    return fake


def _facts(studio):
    """What the panel would send, read as a command would, under the lock."""
    with studio.lock:
        return studio.panel("claude")._current_facts()


def _said(studio):
    said = []
    original = studio.say
    studio.say = lambda text: (said.append(text), original(text))
    return said


def _ask(studio):
    studio.call("claude.ask")
    assert studio.jobs.wait(30)
    return studio.call("claude.describe")


# --------------------------------------------------------------- the request

def test_the_request_asks_for_the_answers_shape_in_the_windows_language():
    facts = {"measurements": {"peak_dbfs": -3.0}}
    kwargs = claude.request("claude-sonnet-5-5", facts, "zh_TW")
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": claude.SCHEMA}
    assert "Traditional Chinese as used in Taiwan" in kwargs["system"]
    assert json.loads(kwargs["messages"][0]["content"]) == facts
    # parameters that some current model refuses are never sent
    assert not {"temperature", "top_p", "top_k", "thinking", "tool_choice"} & set(kwargs)


def test_effort_and_the_refusal_fallback_go_only_to_models_known_to_take_them():
    facts = {"measurements": {}}
    recommended = claude.request(claude.RECOMMENDED_MODEL, facts, "en")
    assert recommended["output_config"]["effort"] == "medium"
    assert (recommended["fallbacks"], recommended["betas"]) == ("default", [claude.FALLBACK_BETA])
    # a model the Models API has not described, newer than this release
    unknown = claude.request("claude-sonnet-9-9", facts, "en")
    assert "effort" not in unknown["output_config"] and "fallbacks" not in unknown
    listed = claude.request("claude-sonnet-9-9", facts, "en",
                            [{"id": "claude-sonnet-9-9", "effort": True}])
    assert listed["output_config"]["effort"] == "medium"
    haiku = claude.request("claude-haiku-5-5", facts, "en")
    assert "fallbacks" not in haiku           # Haiku has no server-side fallback


def test_auto_is_the_recommended_model():
    assert claude.resolve("auto") == claude.resolve("") == claude.RECOMMENDED_MODEL
    assert claude.resolve("claude-opus-5-5") == "claude-opus-5-5"


def test_only_models_that_return_structured_output_are_offered():
    fake = FakeClient(models=[model("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                              model("claude-old-1", "Old", structured=False),
                              model("claude-haiku-5-5", "Claude Haiku 5.5", effort=False)])
    assert claude.list_models(fake) == [
        {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "effort": True,
         "lifecycle": "active"},
        {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "effort": False,
         "lifecycle": "active"}]


# ------------------------------------------------------------ what is sent

def test_what_is_sent_is_measurements_and_nothing_that_names_the_file(studio, tone):
    rows = studio.call("claude.facts")["rows"]
    labels = [label for label, _ in rows]
    assert labels[:5] == ["Duration", "Sample rate", "Channels", "Edited since opened", "Peak"]
    facts = _facts(studio)
    text = json.dumps(facts)
    assert tone.name not in text and str(tone.parent) not in text
    assert facts["measurements"]["sample_rate_hz"] == 16000
    assert "metrics_panel" not in facts


def test_the_metrics_table_goes_only_while_it_is_of_the_audio_as_it_is(studio):
    studio.call("metrics.inspect")
    studio.state()
    sent = _facts(studio)
    assert sent["metrics_panel"] and sent["metrics_panel"][0]["measure"]
    studio.session.set_selection(0.1, 0.2)
    studio.session.delete()
    studio.state()
    assert "metrics_panel" not in _facts(studio)


def test_the_rows_listed_are_the_figures_sent():
    facts = {"measurements": {"duration_s": 1.5, "peak_dbfs": -0.1, "edited_since_opened": True,
                              "kudio_problems": ["3 clipped samples"]},
             "metrics_panel": [{"measure": "WADA-SNR", "value": "18.2 dB"}]}
    rows = dict(map(tuple, shown_rows(facts)))
    assert rows["Duration"] == "1.50 s" and rows["Peak"] == "-0.1 dBFS"
    assert rows["Edited since opened"] == "yes" and rows["Loudness"] == "—"
    assert rows["kudio found"] == "3 clipped samples"
    assert rows["Metrics: WADA-SNR"] == "18.2 dB"


# ------------------------------------------------------------------ asking

def test_an_answer_is_read_back_with_its_cost(studio, client):
    info = _ask(studio)
    answer = info["answer"]
    assert answer["summary"] == ANSWER["summary"]
    assert answer["next_steps"][0] == {"panel": "effects", "action": "Raise the gain by 8 dB."}
    assert (answer["input_tokens"], answer["output_tokens"]) == (1200, 300)
    assert answer["cost"] == pytest.approx((1200 * 2.0 + 300 * 10.0) / 1e6)
    assert client.sent[0]["model"] == claude.RECOMMENDED_MODEL
    assert info["stale"] is False


def test_an_answer_about_audio_that_has_since_changed_says_so(studio, client):
    _ask(studio)
    studio.session.set_selection(0.1, 0.2)
    studio.session.delete()
    studio.state()
    assert studio.call("claude.describe")["stale"] is True


def test_the_chosen_model_is_asked(studio, client):
    studio.call("options.set", ["claude_model", "claude-opus-5-5"])
    _ask(studio)
    assert client.sent[0]["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("reply, said", [
    (SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                     content=[], usage=None, model="m"), "Claude declined to answer (cyber)."),
    (SimpleNamespace(stop_reason="max_tokens", stop_details=None, content=[], usage=None,
                     model="m"), "The answer ran past its length limit and was cut off."),
    (SimpleNamespace(stop_reason="end_turn", stop_details=None, usage=None, model="m",
                     content=[SimpleNamespace(type="text", text="not json")]),
     "The answer could not be read."),
])
def test_an_answer_that_cannot_be_used_is_said_in_words(studio, client, reply, said):
    client.replies = [reply]
    info = _ask(studio)
    assert info["error"] == said and info["answer"] is None


def test_without_a_key_nothing_is_asked(studio, client):
    studio.panel("claude").keys = MemoryKeys("")
    said = _said(studio)
    studio.call("claude.ask")
    assert not studio.jobs.running() and not client.sent
    assert said[-1] == "No API key yet. Add yours in Edit → Settings…"


def test_without_the_sdk_the_panel_says_what_to_install(monkeypatch):
    """KudioStudio installs it; a copy set up with --no-deps has none."""
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "anthropic" else real(name, *a))
    assert claude.missing() == ('Asking Claude needs the anthropic package: '
                                'pip install "anthropic>=2,<3" keyring')


# ------------------------------------------------------------- the API says no

def test_every_refusal_from_the_api_is_one_sentence():
    anthropic = pytest.importorskip("anthropic")
    cases = [
        (_http_error(anthropic.AuthenticationError, 401), "The API key was refused"),
        (_http_error(anthropic.PermissionDeniedError, 403), "This key may not use m."),
        (_http_error(anthropic.PaymentRequiredError, 402), "no credit left"),
        (_http_error(anthropic.RateLimitError, 429, {"retry-after": "7"}), "try again in 7 s."),
        (_http_error(anthropic.InternalServerError, 500), "(HTTP 500)"),
        (anthropic.APIConnectionError(request=None), "No connection to the Anthropic API."),
    ]
    for error, words in cases:
        assert words in str(claude.said(error, "m")), type(error).__name__
    gone = claude.said(_http_error(anthropic.NotFoundError, 404), "claude-x-1")
    assert gone.retired and str(gone) == "claude-x-1 is not available any more."


def test_a_retired_model_falls_back_to_the_recommended_one(studio, client):
    anthropic = pytest.importorskip("anthropic")
    studio.call("options.set", ["claude_model", "claude-sonnet-4-1"])
    client.replies = [_http_error(anthropic.NotFoundError, 404), ANSWER]
    said = _said(studio)
    info = _ask(studio)
    assert [k["model"] for k in client.sent] == ["claude-sonnet-4-1", claude.RECOMMENDED_MODEL]
    assert info["answer"]["summary"] == ANSWER["summary"]
    assert studio.options["claude_model"] == "auto"
    assert "claude-sonnet-4-1 is not available any more; Settings is back on automatic." in said


# --------------------------------------------------------------------- the key

def test_a_key_is_kept_in_the_credential_store_and_shown_by_its_end(studio, prefs_text):
    panel = studio.panel("claude")
    panel.keys = MemoryKeys("")
    studio.call("claude.save_key", ["  sk-ant-api03-" + "k" * 40 + "abcd  "])
    status = studio.call("claude.describe")["status"]
    assert (status["source"], status["hint"]) == ("saved", "…abcd")
    assert "sk-ant" not in prefs_text()
    studio.call("claude.remove_key")
    assert studio.call("claude.describe")["status"]["source"] == "none"


def test_a_paste_that_is_not_a_key_is_not_saved(studio):
    panel = studio.panel("claude")
    panel.keys = MemoryKeys("")
    said = _said(studio)
    studio.call("claude.save_key", ["my password"])
    assert panel.keys.get() == ""
    assert said[-1] == "That does not look like an API key; one starts with sk-ant-."


def test_a_key_in_the_environment_is_used_without_saving_one(studio, monkeypatch):
    studio.panel("claude").keys = MemoryKeys("")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")
    assert studio.call("claude.describe")["status"]["source"] == "environment"


def test_test_lists_the_models_and_keeps_them_for_the_next_start(studio, tmp_path, monkeypatch):
    fake = FakeClient(models=[model("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                              model("claude-opus-5-5", "Claude Opus 5.5")])
    monkeypatch.setattr(claude, "make_client", lambda keys: fake)
    said = _said(studio)
    status = studio.call("claude.test")["status"]
    assert [m["id"] for m in status["models"]] == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert said[-1] == "The key works: 2 models can answer here."
    again = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    try:
        again.call("options.set", ["claude_model", "claude-opus-5-5"])
        assert again.call("claude.describe")["status"]["model_name"] == "Claude Opus 5.5"
    finally:
        again.shutdown()


def test_a_next_step_opens_its_panel(studio):
    q = studio.events.subscribe()
    studio.call("claude.show", ["enhance"])
    studio.call("claude.show", ["not-a-panel"])
    tools = []
    while not q.empty():
        e = q.get_nowait()
        if e["type"] == "tool":
            tools.append(e["id"])
    assert tools == ["enhance"]


@pytest.fixture
def prefs_text(tmp_path):
    return lambda: (tmp_path / "window.json").read_text(encoding="utf-8")


# ----------------------------------------------------------------- the page

def test_the_page_shows_the_answer_and_a_button_for_each_panel(studio, client):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    info = _ask(studio)
    from test_enhance import PAGE
    script = (PAGE + """
Fake.prototype.setRows = function (rows) { this.rows = rows; };
Fake.prototype.show = function (text, tone) { this.shown = text; this.tone = tone; };
globalThis.document = { createElement: (tag) => new Fake(tag) };
const info = __INFO__;
const ctx = { ui, t: (text) => text, memory: {}, window() {},
  call: async (name) => (name === "describe" ? info : (name === "facts" ? { rows: [], version: 0 } : null)) };
const { mount } = await import(__MODULE__);
const panel = await mount(new Fake("root"), ctx);
panel.state({ has_audio: true, busy: [], version: 0, options: { claude_model: "auto" } });
const lists = made.filter((el) => el.kind === "ul" || el.kind === "ol");
console.log(JSON.stringify({
  findings: lists[0].children.map((li) => li.children.map((c) => c.textContent)),
  steps: lists[1].children.map((li) => li.children.length),
  ask: made.find((el) => el.kind === "button" && el.given?.primary).disabled,
}));
""").replace("__INFO__", json.dumps(info)).replace(
        "__MODULE__", json.dumps((STATIC / "js" / "panels" / "claude.js").as_uri()))
    done = subprocess.run([node, "--input-type=module"], input=script, check=False,
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["findings"] == [["Worth knowing · Quiet", "-31 LUFS"]]
    # the step in Effects has its button; the one outside KudioStudio has none
    assert seen["steps"] == [2, 1]
    assert seen["ask"] is False


def test_the_panel_and_what_it_says_read_in_chinese():
    import re

    from kudiostudio import i18n

    page = (STATIC / "js" / "panels" / "claude.js").read_text(encoding="utf-8")
    words = set(re.findall(r'(?:\bt|ui\.\w+)\("([^"]+)"', page))
    words |= set(re.findall(r'\b\w+: "([A-Z][^"]+)"', page))          # severities, panel titles
    words |= {"Sent", "Value"}
    settings = (STATIC / "js" / "settings.js").read_text(encoding="utf-8")
    words |= set(re.findall(r'(?:\bt|ui\.\w+|rows\.row)\("([^"]+)"',
                            settings[settings.index('group("Claude")'):]))
    words |= set(re.findall(r'tip: "([^"]+)"', settings[settings.index('group("Claude")'):]))
    said = ['Asking Claude needs the anthropic package: pip install "anthropic>=2,<3" keyring',
            "Claude answered: 1200 tokens in, 300 out, about $0.005.",
            "claude-x-1 is not available any more; Settings is back on automatic.",
            "Too many requests for now; try again in 7 s.",
            "The Anthropic API could not answer (HTTP 503); try again later.",
            "Claude declined to answer (no reason given).",
            "The key works: 3 models can answer here."]
    # two names the Chinese window keeps as they are, as its rail does
    left = sorted(w for w in words | set(said)
                  if w not in ("Claude", "Manifest") and i18n.translate(w, "zh_TW") == w)
    assert not left, left


def test_the_credential_store_is_read_once_a_run(monkeypatch):
    """The panel asks where its key comes from at every state, and the
    macOS keychain can ask the user about each read."""
    import sys
    reads = []
    store = {}
    fake = SimpleNamespace(
        get_password=lambda service, user: (reads.append(user), store.get((service, user)))[1],
        set_password=lambda service, user, key: store.__setitem__((service, user), key),
        delete_password=lambda service, user: store.pop((service, user)))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    keys = claude.KeyStore()
    assert keys.get() == "" and keys.get() == ""
    assert keys.set("sk-ant-api03-" + "k" * 40)
    assert keys.get().startswith("sk-ant-") and len(reads) == 1
    assert store == {(claude.KEYRING_SERVICE, claude.KEYRING_USER): "sk-ant-api03-" + "k" * 40}
    keys.delete()
    assert keys.get() == "" and store == {} and len(reads) == 1
