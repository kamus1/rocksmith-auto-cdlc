# Tab ↔ audio alignment: diagnosis and plan

**Status (2026-10-24, 2nd session):** `cdlc build` uses the **hybrid alignment** (`align_hybrid`) with a **tempo DP** in the coarse stage. Benchmark: dev 1.2% of bars off by more than ½ beat (11/12 songs good); validation 6.7% (10/12). **Validated in the game** (MoP and SNA, 2026-10-24). Afterwards, the user noticed that the **end of SNA went out of sync**: the recording has a riff cycle (~4 s) that the tab does not have, and the tempo DP, which chains the bars without gaps, pulled everything after it earlier (it ended at 230.6 s; the music, at 235). Fixed with gaps (point 4 of the method); it now ends at 234.4 s. Deployed `rsTheWhSeven_v8`, pending testing.

**Master of Puppets with the new method** (tab ver 7, 10 s windows against Nacholede): median 10 ms, 0% of windows > 100 ms, 49/49 reliable windows. It fixes the intro (+210 → −30 ms) and the ending (−2,850 → +30 ms) of the previous method. It takes 118 s (60 before). The memory peak, 2.8 GB, is the same as before and comes from the full DTW of `chroma_warp`, which now only provides the global tempo: it could be replaced with something cheap (rerunning the benchmark).
**Seven Nation Army:** matches the validated grid (median 9 ms, maximum 48 ms). The previous hybrid drifted up to 4.7 s in the last 18 bars.
Deployed for testing: `rsMetalMaste_v2_p.psarc` and `rsTheWhSeven_v7_p.psarc`.

## Current method: `align_hybrid`
1. **Harmony DTW** (`chroma_warp`): only to estimate the global tempo ratio (median).
2. **Tempo DP** (`tempo_dp`), the coarse stage. State at each bar boundary = (instant on a 25 ms grid, tempo ratio in 1.5% steps, ±35% around the global one). Each bar lasts its nominal length × the ratio. The tempo can change by up to ±3 steps (±4.5%) per bar at almost no cost (0.002 × steps²). A larger change (section change) costs 1.0. Emission = cosine between the tab chroma (16 instants per bar) and the audio CQT chroma, minus its mean over the whole audio. Unsmoothed chroma. The lag of the audio chroma relative to the attack (160 ms) is compensated at the end. Constraint: no note before the audio starts sounding. Empty bars at the ends extend the tempo of the ends.
   - This way it cannot skip cycles of repeated chords: to gain a cycle it would have to speed up unrealistically.
   - The **fine grid is essential**: with 100 ms frames, a 1.6 s bar could only change its duration in steps of ~6%, and the tempo model parameters did nothing.
   - The useful model is the **band limit** plus the section jump, not the quadratic penalty: with more rigidity (0.02–1.0), songs with variable tempo get worse.
3. **Fine step by dynamic programming:** each bar shifts up to ±2.5 beats over the coarse curve. Emission = drum hits from the tab by type (kick → lows, snare → mids, cymbals → highs) plus guitars and bass with low weight. The cost of changing the shift between bars is quadratic. Envelopes compensated by 40 ms for the onset peak lag.
   Also, **fine harmony**: each bar is split into 4 chunks and the tab chroma in each chunk is compared with the audio CQT chroma in the corresponding interval (weight ×3). It resolves passages of isolated chords with rests (F5/E5 at 1:50, 3:10 and 7:40), where drums alone shifted by two beats.
   **Anchor** to the coarse curve: 0.3 × (shift in beats)². Without it, in stretches of ambiguous rhythm it drifted to ±1 beat. Sweep: 0.1 → 3.4%, 0.3 → 1.9%, 1.0 → 3.3%.
4. **Gaps** (audio that is not in the tab). The tempo DP allows, between two bars, a stretch of audio without tab at a cost of 0.2: a "wait" state (accumulated minimum of the cost) with 2 bits per state for backtracking. Two hypotheses are computed, with the proposed gaps and with none, and the **fine step cost**, which sees the attacks, decides. Chroma alone accepted false gaps: in the MoP outro it placed the final chords over the dying resonance. RsBuild receives `bar_ends_ms`: the bar before the gap lasts the normal length, the gap has no notes and beats at the same tempo, and the report warns about it.
   **Tail cut** (the recording ends before the tab, e.g. a fade-out over a written ending): the tempo DP may stop at a bar boundary within 3 s of the audio end, in the second half of the tab, at 0.1 per dropped bar with notes and dropping at least 4 of them. Without it, the end constraint squeezes the whole second half of the song. The fine step and the intermediate stage give the dropped bars no emission, `tail_cut_bar` goes in `bar_times.json`, RsBuild leaves those bars out and the report warns about it.
   Also: no note after the end of the audio, and frames more than 50 dB below the maximum have no chroma (normalized background noise looked like anything).
5. Start constraint in the fine step too, with a **finite penalty**. With `inf`, if no shift satisfied it, all costs were `inf` and `argmin` returned index 0: the whole song was shifted −2.5 beats.

Results of the previous method (DTW + smoothing; 10 s windows against Nacholede): new tab → median 10 ms, 0% of windows > 300 ms. Old tab → median 20 ms, 27% > 300 ms (in the parts where the tab does not match the recording). Seven Nation Army: matches the validated grid (11 ms) except at the end, where the hybrid gets the end of the music right (235 s versus 230 s).

(The first ~5 s of MoP, at +210 ms with this method, are solved by the tempo DP. The old methods `align_beats`, `align_beats_dp`, `refine_local`, `onset_error` and `smooth_warp` were removed from the code; they remain in the git history.)

## Real reference
Master of Puppets CDLC by *Nacholede* (CustomsForge) in `samples/Metallica_Master-of-Puppets_Nacholede_v1_p.psarc`: audio and beat map synced by hand.

To prepare the reference:
- SNG charts → XML: `build/cmp/sng2xml.fsx`.
- Audio: `.wem` → `.ogg` with `ww2ogg` and `revorb`, which are in `external/Rocksmith2014.NET/src/Rocksmith2014.Audio/Tools/win`.

Evaluator in `bench/eval_notes.py`; `bench/mop.py` aligns MoP and evaluates it. For each 10 s window it looks for the shift that makes the most notes **of equal pitch** coincide (±40 ms) between our chart and the reference one. This tolerates parts that the tabs transcribe differently. Windows with less than 30% matches are marked as unreliable.

## Results on Master of Puppets (median |error| per window)
| Method | Error | Windows > 300 ms |
|---|---|---|
| Beat grid + DP (`align_beats_dp`, the one in `cdlc build`) | ~840 ms | 75% |
| + `refine_local` (deployed `_v3` version) | ~820 ms; also adds a bias of ~35 ms | 77% |
| DTW with CENS chroma | ~380 ms | 62% |
| DTW with CENS chroma + snapping to the nearest beat | ~310 ms | 54% |

By beats, the grid is only right between 1:30 and 2:20. In the rest of the song it is off by 0.5 to 3 s.

### With a better tab (`samples/Metallica - Master Of Puppets (ver 7 by cfhbyron666).gp`)
This tab has 11/16 bars (the old one used 5/8 and was missing a sixteenth note in 36 bars), a written tempo of 210/104/212 and 416 bars without repeats.
| Method | Error | Windows > 300 ms |
|---|---|---|
| **Unaligned** (written tempo, starting at the 1st note) | ~390 ms; smooth drift (+900 / −450), **no jumps** | 59% |
| Beat grid + DP | ~1,410 ms | 92% |
| Grid + DP + local tempo | ~1,690 ms | 84% |
| CENS chroma + snapping to the nearest beat | **~260 ms**; many windows at 0–40 ms, the failures are **+1 beat** (~300 ms) | 25% |

Conclusions:
- The new tab is structurally correct: nothing is missing and nothing is extra. The problem is the aligner.
- The beat grid is useless on MoP.
- Chroma gets the bar right, but snapping to the nearest beat sometimes picks the next one. The beat must be chosen with tempo continuity, not by proximity.

## Lessons
- **The onset/drum metric is misleading** with repetitive riffs: a shift of one or more beats scores almost the same. Do not use it to decide. Use real references.
- **beat_this beats** are good in regular stretches, but in the intro, the interlude and the ending of MoP they skip or invent beats (intervals ×0.5 and ×2). Each error shifts everything after it if the grid is followed to the letter.
- **`refine_local` worsens** fine precision, because the peak of the onset envelope comes after the attack. Remove it or correct the bias.
- **Chroma** distinguishes bars with the same rhythm (the chromatic riff E–D#–D–C#), but on its own it is imprecise (~1 beat).
- Comparing beats by index does not work: the references mark beats in other units (5/8 in eighth notes, the interlude at double tempo).

## Plan
1. **Benchmark** with several references. Besides MoP, take 10–20 CDLC from the library (`dlc/`) with a Guitar Pro tab available. If there is no tab, use the reference chart itself as the "tab" with nominal times (beats per bar at a fixed tempo) to test only the alignment.
2. **Hybrid alignment in a single DP over bars.** Emission = bar chroma (tab versus audio, CENS) + onsets. Candidates = positions on the beat grid (half beat). Transition with local tempo continuity and jumps to correct missed or invented beats.
3. Remove `refine_local` or replace it with an unbiased adjustment (compare the start of the attack, not the peak).
4. Make the `cdlc build` report warn when the alignment is doubtful (bars with absurd durations, disagreement between chroma and beats).
5. Validate in the game: Master of Puppets and Seven Nation Army.

## Benchmark (24 official songs/CDLC with a hand-made beat map)
- Selection (`bench/select_v1.py` over `bench/survey.fsx`): 4 groups × 6 songs (metronome or variable tempo × regular or irregular meter), one per artist, split 3/3 between **dev** and **validation**. Validation is not looked at while tuning.
- `bench/extract.fsx` extracts charts and audio; `bench/run.py` builds a fixed-tempo "tab" with the same structure and notes (Lead + Rhythm + Bass, **no drums**), aligns it and compares each bar with notes against the real one.
- Usage: `.venv/Scripts/python bench/run.py --set dev --tag name [--params '{"onset_lag_ms": 20}']`.

### Results (dev, 12 songs)
| Version | Median of medians | Bars off by more than ½ beat | Good songs (<5% of bars wrong) |
|---|---|---|---|
| Hybrid as it was after MoP (counting all bars) | 22 ms | 33.6% | 0/12 |
| + harmony only in the stretch with notes, measuring bars with notes | 17 ms | 25.7% | 4/12 |
| + general start constraint | 17 ms | 25.7% | 4/12 |

### General failures found
1. **Empty bars at the start** (count-in) shifted the harmony DTW → fixed: the DTW only uses the stretch between the first and the last note.
2. **The preference "the first note coincides with the start of the audio" was overfitting to MoP** → replaced by a constraint that is always true (no note before the audio starts sounding).
3. **Repetitive music (identical chord cycles): the DTW skips entire cycles** (Deep Blue Something: −2 bars between bar 11 and bar 31; Machine Head: seconds of error). The fine step (±2.5 beats) cannot correct it. **Pending:** impose a plausible tempo, either by limiting the DTW slope or with a wide search and a second-order tempo model, and measure again with the benchmark.
   **Solved** with the tempo DP (see Current method).
4. **Chroma bias:** the coarse stage placed everything 100 to 300 ms late in every song → unsmoothed chroma and compensated lag.
5. **The fine step drifted to ±1 beat** in stretches of ambiguous rhythm, even when the coarse stage was right (Joy Division) → anchor.
6. **The start constraint with `inf` collapsed the fine DP** (Queensrÿche, −2 beats over the whole song) → finite penalty.
7. **Benchmark bug:** some charts mark 7/8 bars with one beat per eighth note. The benchmark counted them as quarter notes and the bar lasted twice as long → it now counts the ½ units (Queensrÿche and Yes). Long intervals (×2) are left alone: they may be ritardandos.

### Tried and discarded
- **Attacks in the tempo DP emission:** shifts entire songs by several beats (Joy Division −3 s, Machine Head +1.5 s). Without the short radius of the fine step, repetitive riffs fit equally well when shifted.
- **More rigid tempo** (weight 0.02–1.0, or a jump of 3): worsens Machine Head, Mastodon and Lively Ones, which have real tempo variations.

### Results of this session
| Version | Dev: median / >½ beat / good | Validation: median / >½ beat / good |
|---|---|---|
| Starting point (`aacdc94`, DTW + smoothing) | 17 ms / 25.7% / 4 of 12 | 29 ms / 30.0% / 3 of 12 |
| Tempo DP (100 ms frames) + finite start | 11 ms / 5.8% / 7 of 12 | — |
| + 25 ms grid, compensated chroma, anchor 0.3 | 10 ms / 1.9% / 10 of 12 | — |
| + benchmark with bars in eighth notes | **10 ms / 1.2% / 11 of 12** | **14 ms / 6.7% / 10 of 12** |

Validation was run only once, with the final algorithm. The improvement generalizes (30% → 6.7%), but a gap with dev remains, due to two songs.

### Gaps (different structure between tab and recording)
The benchmark did not detect this: its "tab" comes from the chart itself and always has the exact structure. `bench/run.py --drop 2` removes 2 bars from the tab around 60% of the song (the audio keeps them).
| Version | Normal: >½ beat / good | 2 bars dropped: >½ beat / good |
|---|---|---|
| No gaps | 1.2% / 11 of 12 | 20.6% / 1 of 12 |
| Gaps (cost 0.2) decided by chroma | 1.9% / 11 of 12 (2 false) | 8.9% / 8 of 12 |
| **Gaps decided by the fine step** | **1.7% / 11 of 12** (1 false: Mastodon, 18.75 s at b22) | **8.9% / 8 of 12** |

Tried and discarded: raising the gap cost or making it proportional to its duration (SNA and MoP flip at the same time, between 0.3 and 0.5; with 0.05/s it stops detecting the real gaps), and penalizing loud audio after the last note (worsens the normal benchmark to 3.4%, because many recordings keep sounding after the last guitar note).
Validation and MoP: identical, no gaps.
Which of the repeats is the missing one cannot be known in a riff that repeats identically: in SNA the gap ends up before b89.

### Remaining failures (not fixed, to avoid tuning on validation)
- **Mastodon (dev, 6%):** bars 128–150, where the tab has almost no notes (5 of 20 bars, isolated notes). The real tempo is constant and the DP drifts ±5% because nothing holds it.
- **Queen – Love of My Life (validation, 29%):** the coarse stage is right (±0.3 beats) and the fine step goes to +1 beat in slow acoustic stretches (beats of ~780 ms). It is the same failure Joy Division had: the 0.3 anchor is not enough. General idea: make the anchor strength depend on how clear the fine step emission is, or limit the fine step radius in milliseconds as well as in beats.
- **Yes – I've Seen All Good People (validation, 43%):** the coarse stage has an almost constant offset of +1.5 to +2.8 beats over 90 bars of the acoustic part with vocals. The harmony is ambiguous at ±2 beats and the fine step cannot reach.
- Performance: ~20–30 s per song in the tempo DP. Memory for the backtracking pointers: bars × 41 × (duration / 25 ms) bytes, about 350 MB for the 8.5 min of MoP. If it becomes a problem, limit the time window of each bar.


## Session of 2026-11-07: expanded benchmark, anchor in ms and tab intro

### New benchmark
The previous validation set had already been used, so it moves to dev (**24 songs**; the old split stays in `set_v1` of `benchmark.json`, copy in `benchmark_v1.json`). `bench/select_new_val.py` picked **12 new validation songs** (3 per group, no repeated artists): A Perfect Circle, The War on Drugs, Ghost, Elle King, Jim Johnston, Trivium, Spinal Tap, Bach, The Notetrackers (Superman), Third Eye Blind, FUN. and Stereophonics. `bench/select.py` becomes `select_v1.py`: with `bench/` on the path it shadowed the standard `select` module and, when imported, rewrote `benchmark.json`.
New tools: `bench/diag.py` (per-bar error of the coarse stage and the fine step; `--oracle` uses the ground truth as the coarse stage) and `bench/compare.py` (two runs, song by song). The `align_hybrid` result now includes `coarse_bar_times_ms`.

### Changes (both general, now default)
- **Anchor in ms** (`anchor_ref_ms=450`): the fine step anchor measures the shift in units of min(beat, 450 ms) instead of beats. The coarse stage error is in ms (chroma resolution), not in beats: in a slow song (790 ms beat) a one-beat shift is a lot of error and previously cost almost nothing. The minimum ensures the anchor is never weaker than before (with plain 450 ms, Joy Division, which is fast, went from 0.8% to 16.8%).
- **Tab intro** (`lead_slack_bars=2` in `tempo_dp`): the first note cannot sound before the start of the audio plus the duration of the empty bars at the start of the tab, with a margin of 2 bars (added count-in bar). It is a lower bound: a tab that skips the intro is not affected. It removes Mastodon's false gap, which placed the riff over the intro.

### Results
| Version | Dev (24): >½ beat / good | Dev, 2 bars dropped | New validation (12) |
|---|---|---|---|
| Previous (`0b4a9d8`) | 4.2% / 21 of 24 | 8.6% | 5.0% / 9 of 12 |
| + anchor in ms | 3.1% / 22 of 24 | — | — |
| **+ tab intro** | **2.8% / 22 of 24** | **7.0%** | **5.3% / 9 of 12** |

In dev, Queen (29% → 4.6%), Mastodon (13% → 6.3%) and Elvis (2% → 0%) improve, and none gets worse. MoP: identical (10 ms, 0% > 100 ms).
**On the new validation there is no improvement** (5.0% → 5.3%: Bach 1.5% → 4.4%, 2 bars of 73; FUN. 2.7% → 1.8%). Both changes fix failures that existed in dev and do no harm, but the validation failures are of another kind.

Tried and discarded: chroma of the harmonic part (HPSS) in the coarse stage and a more rigid tempo (0.02) for Yes, no effect.

### Remaining failures
- **Yes (dev, 43%):** with the ground truth as the coarse stage, the fine step keeps it (8 of 207 bars wrong) and its cost is clearly better (−454.8 versus −432.0). The failure is in the coarse stage, which drifts up to +2.8 beats over 90 bars of a repeated pattern. The fine step's advantage is small per bar (~0.25 units of `w_ref`) compared with the anchor at 2 beats (~1.2), so it cannot correct it without removing the protection that fixes Queen. Idea: generate several coarse stage hypotheses and choose by fine cost (as with gaps), but a general way to generate them is needed.
- **Spinal Tap (validation, 35%):** after b89 there are ~12 almost empty bars with a real tempo change (beat from 474 to 650 ms); the DP compresses them and everything after comes 16 beats early. Same kind as Mastodon's drift in b129–153.
- **Superman (validation, 16%):** orchestral intro in rubato (beat from 790 to 1,074 ms within a few bars, few notes). The coarse stage starts 19 beats late and chooses a gap (fine cost −335 with the gap versus −331 without it).
- **Common:** stretches with few notes and free tempo. There the tab gives almost no information; the only general thing left is to use more tracks from the tab (the Guitar Pro drums, which the benchmark does not have because it comes from Rocksmith charts).

### Count-in (2026-11-07)
Bars at the start with no pitched notes and only sticks, rim or hi-hat (`COUNT_IN`) are treated as a count-in and their hits are not aligned (`count_in_bars` in the result). In *The Trooper* (Iron Maiden) the tab starts with 4 open hi-hat hits that the recording does not have. The fine step fitted them into the audio and dragged bars 2–3: the first note ended up 350 ms late and those bars were shortened to 1,380 and 1,480 ms versus ~1,540. Now they are 1,580 and 1,530 ms and the first note falls at the start of the audio. From bar 4 onwards nothing changes; MoP and SNA stay identical. The benchmark is not affected because its tabs have no drums.

### Sustained notes and gaps one by one (2026-11-07)
The user tested the Mastodon alignment in the game (*Oblivion*, official charts moved to our timings) and saw that the drift in b129–153 starts with some very long bends. There were two blind spots in the tab:
- **Bends:** the chroma used the unbent note, and the audio sounds up to 2 tones higher.
- **Long notes and ties:** a note only counted in its own bar and for at most 1 s; Guitar Pro tied continuations were discarded. Bars where a note keeps sounding were empty for the alignment.

**Change 1 (`use_held`):** the tab carries a `held` list of notes that sound without a new attack ([t, d, midi]): the bent part of a bend and the continuation of a tied note. They only count for chroma (`_tab_chroma`, `_bar_part_chroma`, `tempo_dp` emission in bars without attacks), not as attacks. It comes from `RsBuild timeline` (Guitar Pro ties and `BendPoints`) and, in the benchmark, from `bendValues` and long notes split per bar.
**Change 2 (`per_gap_select`):** each gap the DP proposes is decided separately. It tries removing it (forbidding ±4 bars) and moving it (forbidding its position and the neighbours), and keeps the option with the best fine cost. Previously all were accepted or none: in Queensrÿche (2 bars dropped) a real gap dragged a false one along, and in Joni Mitchell the gap fell 2 bars early, in a repeated passage, without another position being tried.

| Version | Dev: >½ beat / good | 2 bars dropped: >½ beat / good |
|---|---|---|
| Before (`744ba98`) | 2.8% / 22 of 24 | 7.0% / 17 of 24 |
| **Sustained notes + gaps one by one** | **2.7% / 23 of 24** | **6.5% / 17 of 24** |

In dev only Mastodon changes (6.3% → 3.4%). With 2 bars dropped, Machine Head (22.8% → 15.0%), Mastodon (9.8% → 6.9%) and Queen (7.9% → 6.3%) improve. Queensrÿche (b184 at +0.52 beats) and Norah Jones (b108 at −0.53) each get one bar worse, in zones with 7 and 2 notes; the user accepted the change knowing this. MoP with the real tab (463 tie and bend pieces): the same, 10 ms. SNA: the gap moves from b89 to b96, within the same 2-bar riff that repeats identically, so in the game it lands the same.

Tried and discarded before this, for Mastodon's drift: holding the tempo to the local tempo of the bars with notes (inherits the drift of the first pass; 11 → 28 bars wrong), an alternative curve interpolated over the empty stretches chosen by fine cost (the neighbours are shifted too) and an anchor proportional to note density (the fine step only charges for deviating from the shape of the coarse curve, so it follows its drift). With the true curve as the coarse stage the fine step places all 175 bars of Mastodon correctly: the failure left in Yes and Mastodon is in the coarse stage.
Diagnostic tool used (outside the repo): official charts of a benchmark song moved to the timings of our alignment and packaged, to see it in the game.

### Intermediate stage: correcting coarse stage drifts (2026-11-07)
Yes and Mastodon: with the true curve as the coarse stage, the fine step keeps it and its cost is better; the failure is a slow drift of the coarse stage (chroma prefers it in repeated patterns) that the fine step, being anchored, cannot correct.
**`tempo_refine`:** DP with state (shift at the start of the bar, at the end), up to ±4 beats (max. 2 s) over the coarse stage, 40 ms grid, fine step emission (attacks + per-chunk chroma) and a cost for **changing tempo between consecutive bars**, `refine_weight` × (Δ log ratio)²: a constant tempo costs nothing even if it moves away from the coarse stage. It is only a candidate: it goes through the fine step and is accepted if **fine cost gain + `refine_chroma` × change in the coarse stage chroma > `refine_margin`** (1000, 20 and 1.5). Without the combined criterion, Queen (4.6% → 69%: the fine step, unconstrained, shifts the slow stretches by a beat; the chroma gets much worse) and Joy Division (0.8% → 16.8%: small gain) broke. About 8 s more per song.
Measurements the thresholds come from (weight 1000): Yes +25.0 / −0.74 (113 → 24 bars wrong), Queen +9.1 / −1.45 (4 → 50), Joy Division +3.1 / −0.09 (1 → 25), Mastodon +0.1 / −0.05 (20 → 8, rejected by the margin), OutKast +2.9 / +0.01, Joni Mitchell +2.3 / −0.03. Weights tried: 30 (does not fix Yes), 300 (breaks Cake, Machine Head…), 3000 (worse than 1000).

| Version | Dev (24) | 2 bars dropped | New validation (12, never seen) |
|---|---|---|---|
| Before (`f4b4035`) | 2.7% / 23 good | 6.5% / 17 good | 5.5% / 7 good |
| **Intermediate stage** | **1.0% / 23 good** | **4.9% / 17 good** | **4.1% / 9 good** |

Dev: Yes 43.5% → 6.3%, OutKast and Joni Mitchell → 0%; none gets worse. 2 bars dropped: Yes, Sabaton and Queensrÿche improve; OutKast 3.2% → 3.8% (one bar). New validation (third set, `bench/select_new_val.py --seed 23 --prev validation_v2`: Shinedown, All That Remains, Dream Theater, Audioslave, Audrey and Kate, Manic Street Preachers, Testament, Boston, Grateful Dead, Spacehog, Bill Withers, R.E.M.): Manic Street Preachers 10.4% → 0%, Dream Theater 6.4% → 0%; none gets worse. MoP the same (10 ms). **This validation set is now used.**
Remaining in the validation: Boston (18.9%), Audioslave (17.5%), Audrey and Kate (10.6%); not looked at yet.

### Fixed benchmark: complete tabs (2026-11-07)
Three subagents diagnosed Boston, Audioslave and Audrey and Kate and found a benchmark bug, not an algorithm one. `build_timeline` was losing notes from the charts:
- repeated chords (`<chord chordId=…/>` without `<chordNote>`, between 30 and 90% of the chords in many songs), which are now expanded with their `chordTemplate`;
- entire phrases, because a single difficulty level was used (the one with the most notes); now each phrase uses the level of its `maxDifficulty` (`full_events`).
A real Guitar Pro tab includes all of that, so the benchmark was measuring with tabs much poorer than real ones. Examples: Audioslave goes from 1,496 to 7,149 notes.

| Set (complete tab) | With intermediate stage | Without it (`refine_weight` 0) |
|---|---|---|
| Dev (24) | **2.8% / 22 good** | 3.1% / 21 |
| Dev, 2 bars dropped | **4.6% / 21 good** | 5.0% / 20 |
| Validation v3 (12) | **1.8% / 10 good** | 4.5% / 7 |
| Validation v2 (12) | 2.5% / 10 good | 2.5% / 10 |

With the complete tab, the intermediate stage does not worsen any song in any set (Oasis, OutKast, Joni Mitchell, Sabaton, Shinedown, Audioslave, Audrey and Kate, Manic Street Preachers improve). Compared with the incomplete tab, Mastodon, Machine Head, Silverstein, Steve Winwood, Joy Division and Queen improve, and **Yes (6.3% → 49.8%)** and **Disturbed (0 → 10%)** get worse.
**Yes and Disturbed:** the intermediate stage proposes the good curve (always accepting it: Yes 49.8 → 13.2%, Disturbed 10 → 2%), but the criterion rejects it. With complete tabs, Yes (+6.33 fine gain, −1.15 chroma) and Queen (+7.72, −1.16; accepting it takes it from 3.1 to 53.8%) end up at the same point: no criterion based on those two measures accepts one and rejects the other. The shape of the change does not separate them either (fraction of bars shifted by a whole beat: Queen 53%, Disturbed 46%, Yes 17%, Oasis 12%), so nothing is tuned, to avoid overfitting. With negative gain, always accepting makes things worse (Machine Head, Silverstein, Boston, Spinal Tap). `bench/run.py` now stores `refine_gain` and `refine_chroma_change` per song.
Remaining failures, according to the diagnoses: the Boston intro (an A pedal without attacks and rubato: no information), abrupt tempo changes with fermatas (no stage allows jumps that large; idea: a fixed-cost jump in `tempo_refine`), and short or decaying notes, which are weighted poorly in the chroma (idea: a minimum duration in the tab chroma or attenuating `held`).

### Tail cut: recordings that end before the tab (2026-11-08)
Free Bird (studio version, fades out during the solo; the tab writes the full ending, 256 bars, ~10:19 at its tempos versus 9:14 of audio): the end constraint squeezed bars 108–256 into the audio, ~195 BPM where the band plays at a steady ~150 (librosa, 20 s windows). With the tail cut the coarse stage stops at b236, at the audio end, and the solo stays at 150–155 BPM; suspicious bars 128 → 4.
Tail cost: 0.3 does not cut (only the last 5 bars); 0.1, 0.03 and 0 all cut at b236.
The minimum of 4 dropped bars comes from Audioslave (validation): the chart's last bar is at 350.7 s, past the audio-end threshold (−40 dB, 347.0 s), and without the minimum the DP dropped its last 2 bars (1.7% → 10.7% of bars off).
Results with the final version: dev (24) and validation v3 (12) identical song by song to no tail cut (2.8% / 22 good and 1.8% / 10 good); MoP the same (10 ms, 0% > 100 ms).
