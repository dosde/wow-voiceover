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

## Using the collection

`collected_lines.json` is rebuilt from every contribution. Feed it into the generator:

```
python AI_VoiceOver_Continued/Tools/generate_voices.py --import community/collected_lines.json
```

The workflow only validates the texts - what voices are generated, and whether any are
generated at all, is everyone's own decision.
