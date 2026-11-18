# Claude

The other panels measure. This one reads what they measured back to you in
words. **Ask Claude** sends the clip's figures to Claude through Anthropic's
API and shows what Claude makes of them: a paragraph on the recording, the
findings that matter most first, and next steps that name the panel that does
each one, with a button that opens it.

It reads figures, not the recording, and it can be wrong. Take the findings as
a reading of the numbers, and check them by ear and against the panel that
measured them.

## What is sent

Claude takes text and images, not audio, so the recording itself never goes.
The panel lists what does before you ask:

- the duration, sample rate and channel count, and whether the clip has been
  edited since it was opened
- peak, true peak, RMS, loudness and loudness range
- noise floor, estimated SNR, bandwidth, clipped samples, DC offset, the share
  of silence and the count of samples that are not finite
- whatever kudio found wrong with the recording, in its own words
- the rows of the [Metrics](metrics.md) table, when it was made on the audio
  as it is now

No file name and no path go with them. The list follows the audio: after an
edit it is measured again, and a Metrics table from before the edit is left
out, since it is no longer of this audio. An answer about audio that has since
changed says so above it.

## Your key

The panel runs on your own API key. In **Edit → Settings…**, under **Claude**,
**Get a key…** opens the Claude Console in the browser, where keys are made.
Paste the key into **API key**, then **Save the key** and **Test**. Test lists
the models the key can use, which costs nothing, and says how many there are.

The key is kept in the system's credential store (Windows Credential Manager,
the macOS keychain), not in KudioStudio's settings file, and the dialog shows
only its last four characters. A key already in the `ANTHROPIC_API_KEY`
variable, or an `ant auth login` profile, is used without saving anything.
**Remove the key** takes the saved one out of the store.

Every question is billed to that key's account. The line under an answer says
how many tokens it took and, for the models KudioStudio has a price for, about
what it cost; Claude Sonnet 5.5 is $2 per million tokens in and $10 out.

## Which model

**Model** in Settings is **Automatic** until you choose another, and Automatic
is Claude Sonnet 5.5 in this version, the model the answers were tried on. A
later KudioStudio may move it to a newer model, and Automatic moves with it.

After **Test** the list also holds every model the key can use that returns
answers in the fixed shape this panel reads, as Anthropic's Models API lists
them. A model released after this version of KudioStudio can be chosen there
without waiting for a new one.

A chosen model that has since been retired is not a dead end. The panel asks
Automatic's model instead, says which model was gone, and sets the choice back
to Automatic.

## When there is no answer

| The status line says | Meaning |
|---|---|
| No API key yet. Add yours in Edit → Settings… | no key is saved, none is in the environment, and Test has not found one |
| The API key was refused, or there is none. | the key is wrong or has been revoked; make a new one in the Console |
| This key may not use ‹model›. | the key's organisation cannot use that model; choose another, or Automatic |
| The account behind this key has no credit left. | the account needs credit in the Console |
| Too many requests for now; try again in ‹n› s. | the key's rate limit; the wait is Anthropic's |
| No connection to the Anthropic API. | the computer is offline, or a firewall keeps it from api.anthropic.com |
| Claude declined to answer (‹category›). | Anthropic's safety classifiers stopped the answer. On Claude Sonnet 5.5 the question has already been tried on the model Anthropic recommends for such a case |
| The answer ran past its length limit and was cut off. | an answer longer than the panel allows; ask again |
| Asking Claude needs the anthropic package: … | KudioStudio was installed without its dependencies; install the two packages the line names |
