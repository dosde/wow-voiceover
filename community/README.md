# Community lines

Quest and gossip texts that the addon found no recording for - collected while playing,
mostly the content WoW Forever added. Only texts are shared here, no audio.

## Contributing

Run `AI_VoiceOver_Continued/Tools/contribute.py` after playing. It bundles the lines your
client collected, checks them for personal data and opens a pull request that adds one file
under `lines/`. Your character's name, class and race are already replaced by the game's
placeholders (`$N`, `$C`, `$R`) when the addon records a line.

Put `auto_generate.ps1` in your autostart and run `contribute.py` afterwards to do this
without thinking about it.

## How a line gets in

A line is only added once **two different contributors** report the very same text for it.
Until then it waits in `pending_lines.json` with its vote count - sending your collection
again after someone else reported a line is what confirms it.

If contributors disagree about the text, the version in the lead also needs twice the votes
of the runner-up. A single faked contribution can therefore neither enter the collection nor
block a real line. Contributions carry a random id per installation, so votes can be counted
without anyone knowing who contributed what; the GitHub account behind a pull request stays
visible as usual.

## What to voice first

`priorities.md` is rebuilt with every change: it ranks the lines by how many contributors
ran into them, and lists the busiest NPCs. Start there when voicing costs money.

## Using the collection

`collected_lines.json` is rebuilt from every contribution. Feed it into the generator:

```
python AI_VoiceOver_Continued/Tools/generate_voices.py --import community/collected_lines.json
```

The workflow only validates the texts - what voices are generated, and whether any are
generated at all, is everyone's own decision.
