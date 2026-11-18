// Claude: the clip's measurements read back in words, with next steps that
// open the panel that does them.
//
// The figures that would be sent are listed before anything is, and are
// listed again whenever the audio changes. The key and the model are in
// Edit → Settings…; this panel only says which it will use.

const SEVERITY = { major: "Needs fixing", minor: "Worth knowing", ok: "Fine" };
const TITLES = { inspect: "Inspect", effects: "Effects", enhance: "Enhance", compare: "Compare",
                 record: "Record", synthesize: "Synthesize", manifest: "Manifest",
                 pitch: "Pitch", metrics: "Metrics", batch: "Batch" };

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  let busy = info.busy;
  let hasAudio = false;
  let version = -1;

  // ------------------------------------------------------------ asking
  const asking = ui.section("Ask about this recording");
  const who = ui.readout("");
  const sent = ui.table(["Sent", "Value"], { numeric: [1] });
  const ask = ui.button("Ask Claude", { primary: true, onClick: async () => show(await ctx.call("ask")) });
  asking.body.append(
    who,
    ui.hint("Only the figures below go, with your key, to Anthropic's API: no audio, no file name. Claude reads them and says what they mean."),
    sent, ui.buttons(ask));

  // ------------------------------------------------------------ answer
  const answer = ui.section("Answer");
  const stale = ui.readout("");
  const summary = document.createElement("p");
  summary.className = "claude-summary";
  const findings = document.createElement("ul");
  findings.className = "claude-findings";
  const steps = document.createElement("ol");
  steps.className = "claude-steps";
  const caveats = ui.hint("");
  const cost = ui.readout("");
  const failed = ui.readout("");
  answer.body.append(stale, summary, findings, steps, caveats, cost, failed);
  root.append(asking.el, answer.el);

  async function listFacts() {
    const facts = await ctx.call("facts");
    if (!facts) return;
    version = facts.version;
    sent.setRows(facts.rows.map(([label, value]) => [
      label.startsWith("Metrics: ") ? `${t("Metrics")}: ${t(label.slice(9))}` : t(label),
      t(value)]));
  }

  function usable(status) {
    return !status.missing && (status.source !== "none" || status.models.length > 0);
  }

  function show(state) {
    if (!state) return;
    last = state;
    const status = state.status;
    if (status.missing) {
      who.show(t(status.missing), "warn");
    } else if (!usable(status)) {
      who.show(t("No API key yet. Add yours in Edit → Settings… (Ctrl+,); it stays on this computer."), "warn");
    } else {
      const auto = status.chosen === "auto" ? ` (${t("automatic")})` : "";
      const key = status.source === "saved" ? ` · ${t("key")} ${status.hint}` : "";
      who.show(`${status.model_name}${auto}${key}`, "");
    }
    ask.disabled = busy || !hasAudio || !usable(status);

    failed.hidden = !state.error;
    if (state.error) failed.show(t(state.error), "warn");
    const a = state.answer;
    answer.el.hidden = !a && !state.error;
    for (const part of [summary, findings, steps, caveats, cost, stale]) part.hidden = !a;
    if (!a) return;
    // Claude wrote the answer in the window's language; only the words
    // around it are translated here
    stale.hidden = !state.stale;
    if (state.stale) stale.show(t("The audio has changed since this answer."), "warn");
    summary.textContent = a.summary;
    findings.replaceChildren(...a.findings.map((f) => {
      const li = document.createElement("li");
      li.className = `severity-${f.severity}`;
      const label = document.createElement("strong");
      label.textContent = `${t(SEVERITY[f.severity] || f.severity)} · ${f.title}`;
      const detail = document.createElement("span");
      detail.textContent = f.detail;
      li.append(label, detail);
      return li;
    }));
    steps.replaceChildren(...a.next_steps.map((s) => {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = s.action;
      li.append(text);
      if (TITLES[s.panel]) {
        li.append(ui.button(`Open ${TITLES[s.panel]}`, {
          onClick: () => ctx.call("show", [s.panel]),
        }));
      }
      return li;
    }));
    caveats.textContent = a.caveats;
    caveats.hidden = !a.caveats;
    // a new answer is below the figures, out of sight after the click
    if (keyOf(a) !== seen) {
      seen = keyOf(a);
      answer.el.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
    const money = a.cost === null ? "" : ` · ${t("about")} $${a.cost.toFixed(3)}`;
    const by = a.answered_by ? ` · ${t("answered by")} ${a.answered_by}` : "";
    cost.show(`${a.model_name || a.model}${by} · ${a.input_tokens} ${t("tokens in")} · ${a.output_tokens} ${t("tokens out")}${money}`, "");
  }

  let last = info;
  let chosen = info.status.chosen;
  const keyOf = (a) => a.number;
  // the answer there was at mount is not scrolled to; a new one is
  let seen = info.answer ? keyOf(info.answer) : 0;
  show(info);
  return {
    state(s) {
      hasAudio = s.has_audio;
      // a model chosen in Settings: the line above the button names it
      if (s.options && s.options.claude_model !== chosen) {
        chosen = s.options.claude_model;
        ctx.call("describe").then(show);
      }
      busy = s.busy.includes("claude");
      // the figures are of the audio as it is: listed again after an edit
      if (s.has_audio && s.version !== version) listFacts();
      if (!s.has_audio) sent.setRows([]);
      show(last);
    },
    event(e) {
      if (e.info) show(e.info);
    },
    busy(on) {
      busy = on;
      ask.disabled = on || !hasAudio || !usable(last.status);
    },
  };
}
