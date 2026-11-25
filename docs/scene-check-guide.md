# Scene check — is the bench as the plan expects?

**Status: experiment finished on one bench, design proposed, no platform code
yet.** Everything here was measured on the `jude_vision_test` bench on
2026-11-19 and 2026-11-20 with the top camera (OAK-1 W). The numbers are from
one bench, two days and a few dozen photos: they show the method works, not
how well it will work everywhere. Section 11 lists what is still unproven.

## 1. What this is

Two checks that look at the bench from a fixed top camera and compare it with
what should be there. They answer different questions and are built
differently.

| | Check A: place check | Check B: difference sweep |
|---|---|---|
| Question | Is the expected item at this place? | Is anything on the bench that should not be? |
| Knows about | The plan (which item is expected where) and the CAD | Only a reference picture of the bench |
| Looks at | One place at a time | The whole area at once |
| Trained | A small "same or different" layer on a frozen vision model | Either nothing (big model) or a small fast network that copies the big model |
| Typical use | At the end of a step, on the places the step touched | Continuously, for hands, tools and dropped objects |
| Speed on the Jetson | About 6 ms per place | 5 ms per frame (small model), 0.7 s per frame (big model, same detail) |

The rule both follow: a bad state that is reported as good is not acceptable,
and a false alarm is a nuisance to keep rare. So both compare against a
reference of the good state and flag whatever does not match it.

## 2. What both checks share

### 2.1 The camera

- OAK-1 W, 4056 x 3040, fixed above the bench, read through the unit's vision
  server (`ws://<unit>:4001/ws`, `camera_add` then `camera_get_img`).
- **Lens: the camera's own factory calibration, never a lens fitted on the
  bench.** It is stored in the camera and read with depthai
  (`readCalibration`): focal length 2300 px, centre (2029, 1516), OpenCV
  rational distortion with 8 numbers. It is valid to about 56 degrees off
  axis, so the extreme corners of the photo have no usable ray.
- **Position: solved from the fixture-plate holes** with that lens. 536 holes
  land within 0.65 px (median), with no left or right bias. The plate alone
  pins the height: forcing the camera 20 mm lower costs over 20 px.
- A photo from a later session is refitted for position only, on its own
  holes. Between two days the camera had moved 3.7 px.
- **Exposure and white balance are automatic: the camera's own defaults**
  (`camera_add` with no `exposure`, `iso` or `wb`). Nothing is chosen by hand;
  a setting changes only after it is measured on the bench.
  Measured 2026-11-24 on the emptied 2 ml rack with the doors open (daylight):

  | Setting | White rack burnt out to pure white |
  |---|---|
  | auto exposure, auto white balance (settled) | 0.2 % |
  | 30 ms, ISO 540, 3500 K (the old hand-picked pin) | 73 % |
  | 30 ms, ISO 400, 3500 K | 50 % |
  | 30 ms, ISO 300, 3500 K | 4 % |

  Auto exposure keeps 30 ms and lowers the ISO itself. A fixed colour
  temperature fits one kind of light only: at 3500 K daylight turns blue.
  The old pin burnt the rack and the lit side of every hole to white, leaving
  a small grey crescent; that is what made the empty corner place A1 read as a
  vial with a cap (section 3). The 13 test photos and the check-B recording
  were taken with that pin.
- **Skip the first 5 s after the camera starts.** Exposure and white balance
  settle over a few frames: the shot 4 s after `camera_add` was 17 % burnt
  out, the next one 0.4 %. Once settled, two shots of a still bench differ by
  about 4 grey levels of 255 (sensor noise), the same as with white balance
  measured once and held.

Why the factory lens matters: an earlier lens fitted on the plate holes alone
had a focal length 8 % too short. It still aligned the plate, but put the
camera 71 mm too low, so everything above the plate leaned by the wrong
amount, differently on each side of the bench. That looked like racks sitting
6 mm too low in the scene. With the factory lens the tip rack measures 57.8 mm
against the scene's 57.5 mm.

### 2.2 The scene

The scene file and the CAD give, for every place an item can occupy, where it
is and what it looks like from the camera. No per-object rule is written by
hand.

- **Shadow of a place**: the silhouette of its items as the camera sees them,
  alone on the bench. It comes from 18 one-time renders per project (every
  third row and column of items shown, so no item hides another; once with
  caps and once without). Pieces under 5 % of the shadow are dropped: a long
  CAD tip shows through neighbouring rack holes and those specks are noise.
- **Box**: the smallest square around the shadow, enlarged by 40 %. This is
  what is cut from the photo and given to the vision model. The extra area is
  context for the model; it is not compared.
- **Outline**: the shadow minus what the expected neighbours in front cover.
  For any expected bench state it is composed from the one-time shadows by
  painting the present items farthest from the camera's foot first. No render
  per step is needed. Against a full render of each state, 95 % of 3,016
  outlines are identical and the worst overlaps 95 %.

### 2.3 The big vision model

DINOv3 ViT-S/16, frozen, the unit's shared backbone (`dorna_vision`). For a
224 x 224 input it gives a 14 x 14 grid of patch outputs, 384 numbers each.
The shipped engine averages them; the experiment exposes the grid as a second
output of the same network (`patch_engine.py`, tensor `/m/Slice_output_0`).
One input takes 5.9 ms on the Jetson Orin Nano.

## 3. Check A: the place check

> **Current direction (2026-11-24): section 14; the design for a real setup and the vision server: section 15.** A classifier trained on CAD renders only, plus a small adapter
> trained on a few labelled photos per site. The reference-photo method below still works, but needs real
> reference photos of every place and state.

**How it decides.** For one place and the state the plan expects there:

1. Cut the plain box from the photo. Nothing is drawn on it or blanked out.
2. The big model turns it into a 14 x 14 grid of patch outputs.
3. The same grid is already stored for reference photos of that place in the
   expected state.
4. Each patch is compared with the reference patch at the same position.
   Only patches inside the outline count, weighted by how much of the patch
   lies inside.
5. A small trained layer (standardise, then logistic regression on the
   averaged patch differences, 768 inputs) says "same" or "different". The
   best match over the references counts.

**What is trained and from what.** Only step 5. Training pairs are (photo of a
place, reference of that place in some state), labelled "different" when the
item really differs. Each training crop also goes in as three altered copies
(shifted up to 1 mm, scaled by 3 %, turned by 2 degrees, with light, shadow,
blur and noise changed).

**Results** (9 training photos, 1 photo to set the level, 3 test photos never
trained on; 3,171 real changes and 6,384 good cases):

| Setting | Real changes missed | Good cases flagged |
|---|---|---|
| Box = shadow + 40 %, outline from the shadow, altered copies | 0 | 0 |
| The same with the test crops shifted 1.2 mm | 0 | 0 |
| Two small crops per place (item top and seat) instead of the outline | 0 | 0 |

Safety gap in the hardest rack, the tip rack (scores run from 0 = same to
1 = different): highest good case 0.000, weakest real change 0.17 on plain
crops; 0.0003 against 0.11 when shifted 1.2 mm. All other racks keep the
weakest real change above 0.88.

**Settings that were tried and rejected:**

| Tried | Result |
|---|---|
| A CAD render as the reference instead of a real photo | On the chosen method (box x 1.4, outline patches; 2026-11-23, `shadow_ref.py REF=real/cad/mat/skin`), every real change caught in all four. Good cases flagged: real 0, plain CAD 56, CAD with measured materials 163, photo skins on the 2 ml parts 175 (of 6,384), most of them in the tip rack. Shifted 1.2 mm: 0 / 364 / 603 / 133. The 2 ml rack and both cap holders stay at 0 with plain CAD. Pasted objects caught by the untrained distance: real 94 %, renders 4-14 %, because a render differs from a photo in every patch. More realistic renders did not help: materials and skins narrowed the margins on the 2 ml rack. (The first experiment's 5.1 % against 0.4 % came from the retired single-feature pipeline.) |
| Greying out the neighbours in the picture | Worse than leaving the picture alone and selecting patches. |
| A margin around the outline (1, 2, 4 mm) | Only hurts: at 4 mm, 40 good cases flagged. |
| Box = shadow + 10 % | Too tight: under a 1.2 mm shift the tip-rack gap almost closes. |
| Pasted objects in this layer's training | Breaks it: 78 good cases flagged under a shift. Foreign objects belong to check B. |
| One alarm level tuned per rack or per component | Not needed: the same settings worked for all eight rack types, from 7 mm tips to 100 ml bottles. |

**What is still a stand-in.** The labels in this experiment were read back
from hand-arranged photos, with corrections by eye (7 tip labels were wrong
at first: an empty hole next to a seated tip is half covered by that tip's
rim). In a real run the plan gives the label of every place at every step,
and the reference photos come from one good run of the same project.

**Testing a change to check A.** These rules come from real mistakes; the
reason is given with each.

1. **Measure on the chosen method only:** `shadow_check.py` with
   `SHADOW_PAD=1.4` (box, outline, patches, head and altered copies as
   above). The first experiment's scripts (`build_train.py`,
   `compare_ref.py`, `build_mat.py`, `build_group.py`) cut a box with no
   margin and turn the whole crop into one feature, so neighbours count as
   much as the item. On 2026-11-23 the materials and skin references were
   first measured there by mistake, and those numbers said nothing about the
   chosen method. The old scripts now say "RETIRED" at the top.
2. **Reproduce before changing.** Rerun the unchanged method and check that
   it gives the stored result (`out/shadow_p1.4.json`) before trusting a
   variant.
3. **Change one thing.** Add a switch to a copy of the script (for example
   `REF=real|cad|mat|skin`), give every output its own name, and never
   overwrite a stored result.
4. **Look before trusting a number.** Draw the box and the outline on a few
   photo crops and reference crops and look at them at full size. The
   zero-margin crop above would have been seen at once.
5. **Compare like with like.** A photo and a render are compared only in the
   same bench state, with the photo carried into the reference camera's
   pixels (`pairlib.to_photo`). A colour fit once measured a render with
   every slot filled against a photo of another state.
6. **Name the method next to every number** in a report, such as "chosen
   method, real reference", so no number is read against the wrong method.

## 4. Check B: the difference sweep

### 4.1 With the big model, no training

The area is tiled with squares; each is enlarged to the model's input; every
patch is compared with the patch at the same position in one reference photo
of the clean bench. Cells over an alarm level are joined into regions.

Real stray objects placed by hand (one reference photo, four test photos,
whole bench):

| | Result |
|---|---|
| Same light as the reference (3 photos, 18 placed things) | 18 of 18 found, each 2.1 to 3.9 times over the alarm level |
| Hardest ones found | A loose clear tip on a black holder, a cap taken off a bottle, an extra blue cap among blue caps (the weakest) |
| False regions, same light | 4 to 6 small ones per photo, about 0.2 % of the bench; always the robot's glossy cover and the white rail |
| Room about 28 % brighter (1 photo) | 3 of 3 found, but 7 % of the bench flagged, all on white surfaces that burn out |

Speed against detail (whole bench of nine plates, squares not overlapping):

| One patch covers | Time per photo | Placed things found |
|---|---|---|
| 3.3 mm | 3.5 s | 18 of 18 |
| 6.5 mm | 0.72 s | 18 of 18 |
| 13 mm | 0.17 s | 16 of 18 (loses the extra cap) |

Looking coarsely first and zooming in where it differs saved almost nothing
at 6.5 mm (0.53 s against 0.66 s): the coarse pass asks for a zoom too often.

### 4.2 The small fast model

A small network learns to produce the big model's "how different" map
directly from two pictures.

- **Network**: 439,161 parameters. The same small convolutional encoder runs
  on the reference and on the current picture (half-size photo, output one
  eighth of that: one cell = 6.5 mm of bench, 64 numbers per cell). A small
  second part compares the two encodings and gives one "different" value per
  cell. The reference's encoding is computed once and kept.
- **Area**: plates 1, 4, 7, pulled 13 mm inside the plates' outline. With the
  outline itself, whatever shows at the plates' edge (a person sitting behind
  the bench) raised the alarm level from 0.30 to 0.50.
- **Training data**: a ten-minute recording through the vision server, 149
  photos, while a person put things on the bench, moved them and reached
  across. Nobody labelled anything by hand.
- **Labels**, both made automatically:
  1. The big model's own map for pairs of recorded photos, at its 3.3 mm
     look, reduced to the small model's cells.
  2. Cut-outs pasted into training crops with their exact position as the
     label: 48 caps cut at the places the scene gives, and about 500 small
     regions the big model had flagged in the recording.
- **Training**: 4,000 steps on random 384 px crops, about 10 minutes on the
  Jetson itself (PyTorch 2.15 in a separate environment, `~/train_venv`).

Three versions of the same network, tested the same way:

| | 1: big model's 6.5 mm labels | 2: its 3.3 mm labels | 3: 3.3 mm labels + pasted cut-outs |
|---|---|---|---|
| Stray objects of another session found (21 placed) | 19 | 19 | 21 |
| The extra blue cap, times the alarm level | 0.22 | 0.40 | 1.97 |
| Caps pasted on the racks of held-out clean photos (220) | 0 | 24 | 205 |
| Clean photos flagged (share of the plates) | 0.1 % | 0.1 % | 0.1 % |
| Photo taken in a brighter room, share flagged | 6.9 % | 10 % | 23 % |

Version 3 is the one in use. In the same light it flags almost only where the
big model's fine look also flags (0.02 to 0.08 % of the plates elsewhere).
Its weakness is light: the sharper it became on small things, the more it
reacts to a change of room light.

**Speed**: 5.1 ms per frame for the three plates with the reference kept,
against 317 ms for the big model at the same detail. Measured in PyTorch at
half precision; it has not been built as a TensorRT engine yet.

**Several frames in a row.** On 22 clean frames, 146 of 171 flagged cells
were flagged again in the next frame (a tube the person had turned, a real
change) and 7 appeared once. A "two frames in a row" rule removes that rare
flicker and nothing else. Time helps more for telling a passing hand from an
object left behind; it does not help against a light change.

### 4.3 The live test page

`live_demo.py` on the Jetson serves a page (port 8126) that shows plates 1, 4,
7 live, takes the current bench as the reference on a button press and boxes
whatever differs afterwards, with version 3. It runs at about 2 frames per
second. The limit is fetching a full 12-megapixel JPEG from the vision server
for every frame (350 to 600 ms); the model takes about 6 ms. A build that
receives a half-size picture directly from the camera would be limited by the
camera's 10 frames per second instead; that has not been built (section 10).

## 5. Where the labels come from

No label in this work was drawn by a person. Each source below is free once
the system runs, which is what makes both checks improvable with use.

| Source | What it labels | Used by |
|---|---|---|
| The plan | Which item is expected at every place after every step | Check A (not yet wired: the experiment read labels back from photos) |
| The scene and CAD | Where each place is, its shadow, box and outline | Check A, and the cut-out positions for check B |
| A good run | Reference photos of every place in every state it passes through | Check A |
| The big model as teacher | A "how different" map for any pair of photos | Check B, small model |
| Pasted cut-outs | The exact spot of a small object on a rack or plate | Check B, small model |
| The recorder's phase words | Which photos are of a clean bench | Alarm levels |

Hand labelling is kept for one thing: a small answer key to score against.

## 6. What stays the same everywhere and what is per setup

Fixed for every setup and every component: the box rule (shadow + 40 %), the
speck rule (5 %), the patch comparison, the trained layer of check A, the
small model of check B.

Collected again per setup, automatically: the camera's factory lens and its
position from the plate holes, the 18 one-time renders, the reference photos
from one good run, and the alarm level of check B (from how much the clean
bench varies in that run).

Whether the trained parts carry over to another setup without retraining is
not tested. The test is to train on nine setups and check on the tenth.

## 7. How this relates to `sim2real-guide.md`

That design trains classifiers on rendered depth images only, with no real
training data. This work found that for a colour top camera a render is a
poor reference (5.1 % against 0.4 % false alarms) and that real photos
labelled by the plan and the CAD cost nothing to collect. The two are not in
conflict: sim2real is about depth, where the render matches reality closely.
The CAD is essential here too, for where to look and what shape to expect,
just not as the picture to compare against.

## 8. Roadmap

In the order that unlocks the most:

1. **Recorder**: at every step end save the photo and the plan's expected
   state. It gives check A its labels and references, check B its recordings
   and alarm levels, and it is the data for every later improvement. No
   decisions are taken by it, so it carries no risk.
2. **Light**: steadier light, an exposure that does not burn out whites, and
   training on light and shadow changes labelled "same" (section 9: painted
   shadows first, then shadows cast through the CAD, then real frames of the
   robot moving over an unchanged bench).
3. **Silent run**: both checks run on every step and only log what they would
   have flagged. This gives the true false-alarm rate.
4. **Several setups**: pooled training, then the nine-against-one test.
5. **Engine build** of the small model, run inside the vision server on a
   half-size picture straight from the camera (section 10), for the
   continuous sweep. A WebRTC feed for the HMI can follow when it is wanted.
6. **A setup check from one photo**: report components that sit off from the
   scene (the camera found four holders 25 to 50 mm off on this bench).
7. Only then let either check pause the robot, as a warning first.

Larger models become worth trying once several setups are recorded: first a
more capable comparison part for check A, and a bigger vision model only if
the data shows the current one failing. On this bench it has not failed.

## 9. Light and shadow: the plan (not built, not measured)

**Why it matters.** Light is the one weakness that got worse as the small
model got sharper (section 4.2). And once the robot moves in the scene,
masking the robot itself is not enough: its shadow and its reflections fall
on the plates and would be flagged as changes.

**The idea.** Create light and shadow changes ourselves, on a bench we know
is unchanged, and label them "same". The label is free because we made the
change. Three sources, in the order to build them:

| | Source | What it gives | What it needs | What it does not give |
|---|---|---|---|---|
| 1 | Shadows and light painted onto real photos | Soft-edged darkened regions, brightness and colour shifts, with the label "same" | Nothing new; the recordings we have | Shadows of the right shape, glare |
| 2 | Shadows cast through the CAD | A light placed anywhere, the robot in any pose, the shadow computed where it falls in the camera's view and applied to the real photo, label "same" | The scene, the camera fit, the robot's CAD and joint angles | Glare on glass and glossy plastic; detail in burnt-out whites |
| 3 | Real frames of the robot moving over an unchanged bench | The robot's real shadow and real reflections. The plan says nothing on the plates changed and the CAD masks the robot, so everything else is "same" | The recorder, a run with the robot moving | Nothing: this is the real thing |

Check A's training already uses a simple form of source 1 (brightness,
colour, a soft shadow, blur, noise on the altered copies). The small model of
check B had only a 10 % brightness and 4 % colour change, which is why it
floods in a brighter room.

**These "same" labels must override the big model.** The big model reacts to
shadows, so its map would mark them as different. On these samples the label
is "no change", whatever the big model says. That is how the small model can
end up better than its teacher on light.

**Relighting models** (diffusion-based relighting; models that split a photo
into surface colour and lighting) exist and could add variety. Not the place
to start: they are heavy, run offline only, and can quietly erase or invent
small details, which corrupts the "same" labels this plan depends on.

**The risk.** A model taught to ignore shadows may start ignoring dark, flat
objects that look like one, such as a black pen on a dark plate. Every step
of this plan has to be scored on the stray-object answer key (section 4.1),
not only on false alarms.

**A condition.** Burnt-out white cannot be repaired by any of this: a pixel
at pure white has lost what was under it. The exposure has to be lowered
first so the whites keep their detail.

**The first test to run:** retrain the small model with source 1 and measure
it on the brighter-room photo, where it flags 23 % of the plates today, and
on the 21 placed objects, where it finds all 21 today.

## 10. How pictures travel (for the build and a later HMI feed)

**What was measured.** The live test page (section 4.3) fetches a full
12-megapixel JPEG from the vision server for every frame, through the command
meant for single snapshots. That costs 350 to 600 ms per frame: waiting for a
fresh frame, compressing the full picture (about 180 ms), and unpacking it
again (50 to 90 ms). The transfer between two programs on the same machine is
almost nothing; the cost is compressing a picture that never needed to leave
the machine. Lowering the JPEG quality did not help.

**For the checks: do not send pictures at all.** Best first:

1. Run the check inside the vision server, as detections already do. The
   frame is in memory there; only the result travels.
2. Ask the camera for a smaller picture. The OAK can shrink it in its own
   processor; the small model needs half size, a quarter of the data.
3. If the check must be a separate program on the same machine: shared
   memory, with no JPEG in between.

With 1 and 2 the camera's own 10 frames per second should become the limit.
That is expected, not measured.

The processor inside the OAK (an RVC2) can also run small networks on the
camera itself, so that only the result leaves the camera. The small model
might fit; that chip is much weaker than the Jetson and this was not tried.

**For people watching (the HMI): WebRTC, later.** The platform has no WebRTC
today; pictures reach pages as single JPEG snapshots. For a live feed in a
browser page WebRTC is the right tool:

| WebRTC gives | It costs |
|---|---|
| Real video compression: only what changed between frames is sent, roughly a tenth of the data of JPEGs | A connection handshake and a video encoder to run and maintain |
| Low delay, and it adapts when the network gets worse | The Jetson Orin Nano has no hardware video encoder: encoding runs in software (light for the plates strip, heavy for the full picture). The OAK has its own hardware encoder and can deliver an already compressed stream |
| Built into every browser; several viewers without the cost growing as much | The video is lossy |

**The split to keep:**

- The checks use the raw picture inside the vision server. They never look
  at compressed video.
- Results (boxes, counts, alarms) travel as small messages on the connection
  the HMI already has.
- The video is for people only, and the HMI page draws the boxes on top.

Then the video can be compressed as much as the network needs without
touching any decision.

For a small strip at a few frames per second on the bench network, plain
JPEGs are good enough and far simpler. WebRTC earns its place for smooth
video, several viewers, or viewing over WiFi or from outside the bench
network.

## 11. Known weaknesses and open points

- **Light.** One photo in a brighter room flooded check B (7 % of the bench
  with the big model, 23 % of the plates with the small one). White surfaces
  burn out at the current exposure. Matching brightness in software did not
  help.
- **Glossy moving parts.** The robot's cover and the rail give recurring
  false regions; they should be left out of check B using the CAD.
- **Small low-contrast things** (a blue cap on blue caps) are at the limit of
  the 6.5 mm sweep and rest on few real examples: two photos, plus pasted
  tests.
- **One bench, one or two days.** Held-out photos come from the same
  sessions. The robot was never moving in view.
- **Labels of check A** were reconstructed, not taken from the plan.
- **The scene**: caps on the tall bottles sit a few millimetres from where
  the scene puts them (seen in the overlay, not measured).
- **The camera link**: over the long USB cable the camera dropped off once
  during continuous use.
- **Objects on a plate's edge** are partly outside the counted area of
  check B once it is pulled inside the plates.

## 12. Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-11-19 | Compare with real reference photos, not CAD renders | 0.4 % against 5.1 % false alarms (retired pipeline); re-measured on the chosen method 2026-11-23: real 0 good flagged, renders 56-175, and pasted objects 94 % against 4-14 % |
| 2026-11-19 | Bend the CAD into the camera's view; never resample the photo | The CAD is 3D and can be drawn from anywhere; the photo cannot |
| 2026-11-20 | Lens from the camera's factory calibration | The fitted lens put the camera 71 mm too low and misaligned everything above the plate |
| 2026-11-20 | Check A looks inside the CAD outline (the object's shadow), with the box also from the shadow | Works for any object with no per-object rule; matches the two-crop variant |
| 2026-11-20 | No margin around the outline; box = shadow + 40 % | Measured: a margin hurts, a tight box is fragile to a 1 mm offset |
| 2026-11-20 | Foreign objects are check B's job; never train check A on pasted objects | It broke check A |
| 2026-11-20 | Check B gets a small model trained from the big one, with pasted cut-outs | 60 times faster, finds 21 of 21, labels cost nothing |
| 2026-11-20 | Only plates 1, 4, 7 count for check B, pulled 13 mm inside | The row the racks stand on; the edge raised the alarm level |
| 2026-11-20 | Checks run on the raw picture inside the vision server; results go as messages; video (WebRTC, later) is for people only | Fetching a 12-megapixel JPEG per frame cost 350 to 600 ms; compressed video must never feed a decision |
| 2026-11-20 | Light and shadow are handled by training on changes we create and label "same" (section 9), not by a relighting model | Free labels, exact, and the robot's shadow can be cast from its CAD; relighting models can alter small details |
| 2026-11-24 | Camera on automatic exposure and white balance; frames from the first 5 s are not used | The hand-picked 30 ms / ISO 540 burnt 73 % of the white rack out and broke A1; auto: 0.2 %, stable once settled |
| 2026-11-24 | Check A from CAD only: a place classifier (random colour per part, CAD maps, DINOv3 ViT-S) + a per-site adapter trained on a few labelled site photos (section 14) | The base alone missed 7 of 192 places on 4 site photos, all open amber vials; an adapter from those 4 photos cut a new photo from 11 wrong to 1, and photos 6 and 7 came out 48/48 |
| 2026-11-24 | Keep the CAD maps as classifier input | Without them: 44/48 on photo 2, with confident misses on open vials |
| 2026-11-24 | No black and white, no over-exposure added after rendering | 578 and 611 of 624 places right, against 622 without them |
| 2026-11-24 | Whole-rack RF-DETR not used | Trained on the same CAD data: 44/48 on photo 2, calling open amber vials capped at 0.9+ confidence |
| 2026-11-24 | Real setup = check A (CAD base + site adapter) for the state, before/after for each action, check B for anything foreign; unsure or disagreeing means pause and ask (section 15) | Each covers what the others cannot; only check A and check B are measured so far |

## 13. Where the experiment lives

Nothing below is platform code. It is the experiment, kept for re-running.

| Where | What |
|---|---|
| `~/Downloads/scene_monitor/cad/` (this Pi) | Scene dump, render tool (`grab.py`, `serve/view.html`), alignment page (`serve/align.html`, `align_server.py`), camera calibration reader (`read_oak_calib.py`), photo tool (`vision_shot.py`) |
| `~/Downloads/scene_monitor/cad/jetson/` (this Pi; copies run on the Jetson) | Every analysis script. Check A: `pairlib.py`, `label_states.py`, `relabel_tips.py`, `shadow_check.py`, `harden.py`. Check B: `stray_check.py`, `stray_scale.py`, `record.py`, `fast_prep2.py`, `fast_train2.py`, `fast_inset.py`, `live_demo.py` |
| `~/Downloads/scene_monitor/cad/jetson/` (retired) | The first experiment's single-feature scripts: `build_train.py`, `compare_ref.py`, `build_mat.py`, `build_group.py`, `show_skin_fail*.py`. Kept for history, never for judging check A (section 3, "Testing a change to check A") |
| `~/Downloads/scene_monitor/cad/serve/view_mat.html`, `grab_mat.py` | Renders with measured materials (`jude_super2/mats.json`, fitted by `jetson/mat_measure.py`) and, with `&skin=`, the photo skins |
| `~/Downloads/scene_monitor/cad/jude/pairs2/`, `jude/show/` | Result files and pictures |
| `~/scene_test2/` (Jetson) | Photos, recording (`rec/20261120_145910`), trained small models (`rec/fast/student_*.pt`) |
| `~/train_venv/` (Jetson) | The separate PyTorch environment used for training |
| `~/Downloads/scene_monitor/cad/jetson/` (section 14) | CAD-only classifier and site adapter: `rcolv_make.py`, `rcolv_render.py` + `rcolg_render.py` (renders), `rcolv_masks.py`, `rcolv_tokens.py`, `rcolv150_nn.py` (base), `site_labels.py`, `site_tokens.py`, `site_adapter.py`, `site_ft.py` (adapter), `infer_a.py`, `infer_b.py`, `tok_only.py`, `quick.sh`, `quick_site.sh`, `label_sheet.py` (photos); rejected variants: `rcolj_tokens.py`, `rcolg_nn.py`, `stab_*.py`, `rack_*.py`; camera tests: `camtest*.py`, `camana.py`. The Jetson copies in `~/scene_test2/` are the ones that run |
| `~/Downloads/scene_monitor/cad/serve/rcolg_view.html` | Render page with `&rcolpart=` (random colour per part) and `&fast=1` (scene loaded once, `setView()` per shot) |
| `~/scene_test2/rcol/` (Jetson) | Trained weights: base `rows_rcolv150_cls_mask3_224_w.pt`, site adapter `adapter_site5.pt` (40 KB); earlier `rows_rcolj_*`, `rows_rcolx_*` |
| `~/rackshots/` (Jetson) | Site photos `r01`-`r07.png`, their place boxes, labels (`rNN_truth.json`), answers and grids |

## 14. Check A from CAD only, with a site adapter (current, 2026-11-24)

The goal: decide what is in each place (empty, open vial, vial with cap)
**without real reference photos**. The base model is trained on CAD renders
only and ships the same to every site. Each site then trains a small adapter
on a few of its own labelled photos. No images leave the site.

Tested on the 2 ml rack (`vial_plate_3`, 48 places) only.

### 14.1 Results on site photos

Photos `r01`-`r07`, taken 2026-11-24 with the camera on auto exposure and
auto white balance, with racks set at random. Places right out of 48.
Every photo is counted as new to the model unless marked.

| Photo | Base (CAD only) | Base + adapter (4 site photos) | Base + adapter (5 site photos) |
|---|---|---|---|
| r01 | 48 | trained on | trained on |
| r02 | 47 (C1) | trained on | trained on |
| r03 | 46 (A8, E5) | trained on | trained on |
| r04 | 44 (A4, C4, C7, E7) | trained on | trained on |
| r05 | 37 (11 wrong) | **47** (E5) | trained on |
| r06 | not run | not run | **48** |
| r07 | not run | not run | **48** |

Every base mistake was an open amber vial, read as empty or as capped. The
CAD renders have no glass, and open vials were the rarest class in the first
training sets. r06 and r07 were confirmed by eye from the whole-rack label
picture, with 20 and 33 open vials.

### 14.2 How the base model works

1. **Renders:** plain CAD, with **a random colour per part** (every material
   of every component, so a cap's body and top differ) and random light (sun
   direction 0-25 deg from vertical, strength, fill, exposure). Rendered on the
   Jetson GPU with one browser page kept open (`rcolg_view.html` `&fast=1`,
   `setView()`), about 1 s per image. CPU rendering (swiftshader) was about
   5 s, and a fresh browser per state about 9.5 s.
2. **States:** every 2 ml place is independently 1/3 empty, 1/3 open vial,
   1/3 vial with cap (`rcolv_make.py`). The base uses 150 training states x 4
   colourings = 28,800 crops, about 9,600 per class, plus 30 validation states.
3. **Crop:** the place's check box (CAD silhouette x 1.4) at 224 x 224.
   Training jitter: shift up to 3 mm, scale 5 %, turn 4 deg, on 80 % of crops,
   plus `pairlib.light`. Kept in colour.
   **The jitter rule, for every item type (in percentages, so it scales with
   the object):** shift up to **13.3 % of the place's box side** per axis, but
   never more than **21.4 % of the pitch** (centre-to-centre to the nearest
   place); scale ±5 %; rotation ±4°; on 80 % of training crops. Both numbers
   come from the 2 ml rack. There, 3 mm = 7.8 px = 13.3 % of its 59 px box and
   21.4 % of its 36 px pitch. On the tip rack, 13.3 % of its 43 px box would be
   27 % of its 21 px pitch, so the cap applies: 4.5 px, about 2 mm
   (`tip_jitter.py` -> `tip/jitter.json`).
4. **Model:** DINOv3 ViT-S/16, frozen (the unit's TensorRT engine), gives
   14 x 14 x 384 per crop. The **CAD maps** are added to each square: how much
   of it is this place's item, its visible outline, and the neighbours' items
   (the fourth channel, the plan's expected state for this place, is off).
   A 2-layer transformer (width 128) reads the 196 squares and outputs the 3
   classes. 6,000 steps, about 13 min on the Jetson.

### 14.3 How a photo is read

1. **Camera fit** on the plate holes (`infer_a.py prep`): first a coarse
   search for the shift (up to 120 px), then the usual refit. The old refit
   searches only 14 px around each hole. When the camera had moved 35 px it
   locked onto almost nothing (13 holes) and put every place one row off. With
   the search: 586 holes, 0.77 px median.
2. One crop per place, CAD maps of a full-rack plan, one pass. About 7 ms per
   place: DINOv3 about 5 ms, classifier 2 ms.
3. `quick_site.sh <name>` does shot + crops + base + adapter + grid in about
   25 s, mostly loading. `label_sheet.py <name>` draws every answer on the
   whole rack for checking.

### 14.4 The site adapter

- **Base frozen.** Two small bottlenecks start at zero, so at first the model
  equals the base: one on every square's input, one on the class token.
  8,992 numbers, a 40 KB file (`site_adapter.py`).
- **Data:** each labelled site place cut 16 times (1 plain + 15 with the
  training jitter), with the CAD maps used at inference (`site_tokens.py`).
- **Training** (`site_ft.py`): every batch is half site crops, half CAD crops
  from 40 states the base never saw, so the CAD knowledge stays. 1,500 steps,
  about 4 min. After training, the CAD validation errors were still 0 of 1,440.
- **Labels decide everything.** They come from the whole-rack label picture
  (`label_sheet.py`), checked by a person. A single crop is not enough to
  label from: C1 and A8 look different with the whole rack around them.

### 14.5 On-site procedure (next step)

1. Camera on its defaults (auto). Fit the camera on the plate holes.
2. Collect about 10 setups with varied layouts: every kind of item the site
   uses, open and capped, crowded places, empty corners.
3. Read each with the base, draw the label picture, have the operator correct
   it, and save `rNN_truth.json`.
4. Train the adapter on all of them, then test on new setups that were not
   used for training. Freeze base + adapter as the site's model.

### 14.6 What was tried and rejected

All on the 2 ml rack; numbers are places right.

| Variant | Result | Why rejected |
|---|---|---|
| Skin CAD (photo-textured models) | 624/624 on the 13 old photos | Every item needs a hand-made skin: does not scale |
| Random colour per component | 622/624 (A1 wrong) | Per part is the same or better and fits other items |
| Black and white, training and inference | 578/624 | Brightness differences remain; colour helps |
| Over-exposure added to the render image | 611/624 | It clipped caps, not the rack; hurt open vials |
| A brighter fill light | Images barely changed | Not run |
| Plain classifier, no CAD maps (bigger jitter) | r02 44/48, confident misses | Cannot tell its own vial from a neighbour's |
| Input 448 instead of 224 | 10-26 misses on the 13 photos | Only 57 real pixels per place: upscaling adds blur |
| Whole-rack RF-DETR (CAD only, 1,536 images, 10 epochs at 6 min on the Jetson) | r01 48, r02 44 | Called open amber vials capped at 0.9+ confidence |
| Balanced classes alone (vs 18 % open vials) | r02 47 against 48 | No gain alone; kept as the base for the adapter |
| 7-of-9 stability rule (nudge the box 9 times) | 0 silently wrong on r01/r02, 4 places asked | Not needed with the adapter; kept as an option |
| DINOv3 ViT-B/16 | Downloaded, not run | Stopped in favour of the adapter |

### 14.7 Lessons

- **The camera setting broke the first results.** A hand-picked 30 ms /
  ISO 540 burnt the white rack out (73 %), and the empty corner A1 read as a
  cap. Auto: 0.2 % (section 2.1).
- **Bigger jitter mattered.** Training with 1 mm of jitter flipped answers
  when the box moved 3-8 px. 3 mm fixed C1.
- **A few real labelled photos close the gap that renders leave.** The
  amber open vials were the gap here.
- **Practical, on the Jetson:**
  - All data stays on the Jetson; the Pi link is about 1 MB/s.
  - One heavy job at a time, 5 GB cap.
  - User jobs stop when no ssh session is open (linger is off).
  - Keep about 3 GB of disk free; per-epoch checkpoints are 0.5 GB each.

## 15. The design for a real setup (what goes into the vision server)

Agreed 2026-11-24. Three checks with different jobs. A decision rule
combines them so the system is never silently wrong. Nothing here is in the
vision server yet; sections 3, 4 and 14 are the measured parts.

### 15.1 The three checks

| Check | Question | Method | When it runs | Cost | Status |
|---|---|---|---|---|---|
| **A: place state** | What is in each place (e.g. empty / open vial / vial with cap; tip / no tip)? | Base trained on CAD only (per item type) + site adapter (per site), section 14 | Start of a run, after any pause or restart, and periodically | About 7 ms per place (DINOv3 ViT-S about 5 ms + classifier 2 ms); a 48-place rack about 0.35 s | 2 ml rack measured (section 14.1); tip rack in training; other types to do |
| **Before/after** | Did the robot's action change this place the way the plan says? | Compare the place's crop just before and just after the action, inside its CAD outline, with DINOv3 features (the same features as check B). Real photo against real photo, seconds apart: no render-to-photo gap, no training | After every action, for the places it touched | About the cost of check A for one place | Not built, not measured. The real-reference check A (0 false alarms against 56-175 for CAD references, section 3) is the evidence that photo-to-photo comparison works |
| **B: anything foreign** | Is there anything on the bench that should not be there (a dropped cap, a tool, a hand)? | The small fast model compares the current frame with the bench's reference frame (section 4.2) | Continuously or per frame while running; always before a move | 5.1 ms per frame for the three plates | Built and measured (section 4); sensitive to a change of room light |

Before/after cannot know the starting state, and it misses changes that no
action caused. Check A cannot see things that are not in the plan. Check B
does not know what a place should hold. That is why all three are kept.

### 15.2 The decision rule

- **Pass** only when everything agrees: check A matches the plan at every
  place with confidence, before/after shows the expected change, and check B
  finds nothing.
- **Unsure means ask.** A place is unsure when its top probability is below
  a level set from real data (0.9 on the photos so far). The 7-of-9
  stability rule (nudge the box 9 times) is the stricter option: it had 0
  silently wrong answers on r01/r02.
- **Any disagreement means pause** and an operator question on the HMI,
  through the platform's normal pause flow: "C1: expected an open vial, the
  camera reads empty. What is there?", with the crop.
- **Every operator answer is stored** with its crop. These are labelled site
  data for the next adapter update.
- **Confident mistakes are the danger:** they would never be asked. For the
  first runs at a new site, the operator also confirms a small random
  sample of the "sure" places, so those mistakes get found and labelled.

### 15.3 Installing at a site

1. **Camera:** its defaults (auto exposure, auto white balance). Frames from
   the first 5 s after it starts are not used (section 2.1). Lens from the
   camera's factory calibration.
2. **Camera fit:** refit on the plate holes for each photo, with the coarse
   shift search first (section 14.3). The camera moved 35 px between two
   sessions here.
3. **Check A, for each rack type used at the site:**
   1. Take about 10 setups with varied layouts: every item kind, open and
      capped, crowded places, empty corners.
   2. Draw the base model's answers on the whole rack (`label_sheet.py`).
      The operator corrects them. A VLM can pre-fill this sheet, but that
      has not been measured yet.
   3. Train the site adapter: about 4 minutes on the Jetson, a 40 KB file.
   4. Test on new setups that were not used for training. Freeze.
4. **Check B:**
   1. Capture the bench's reference frame.
   2. If the site's light or bench differs a lot from ours, record about
      10 minutes while a person moves things, and retrain the small model
      (about 10 minutes, section 4.2).

### 15.4 Building it into the vision server

- **Where it runs:** inside the vision server, on the raw frame in memory.
  Only results travel, as small messages (section 10). There's no JPEG
  between the camera and a check.
- **Model files:**
  - one check-A base per item type (DINOv3 ViT-S is the unit's shared
    TensorRT backbone; the classifier weights are about 1.4 MB);
  - one site adapter per item type and site (about 40 KB);
  - check B's small model;
  - the camera fit and the bench reference frame.
- **What the workspace gives it:**
  - the plan state of every place;
  - action events with the places they touch: "before" is taken when the
    action starts, "after" when the robot is clear of the view.
- **What it sends back:**
  - per place: answer, confidence, sure / unsure;
  - per action: pass / fail;
  - from check B: boxes with scores.
- **Operator questions:** a pause with the crop and the choices, through the
  HMI (`docs/hmi-guide.md`). The answer is stored for the next adapter update.
- **Adapter update job:** retrain from the stored answers on demand or at
  night. Then test on held-out site photos before switching to the new
  adapter.
- **Same rules everywhere:**
  - the jitter rule as percentages: shift 13.3 % of the box side, at most
    21.4 % of the place pitch, scale ±5 %, rotation ±4°, on 80 % of crops
    (section 14);
  - a random colour per part;
  - balanced classes;
  - CAD maps as an input.

### 15.5 Open before building

- Before/after: build it and measure it on real robot actions.
- The "unsure" level: set it from more real photos, per item type.
- The tip rack: adapter and test (in progress); then the other item types.
- Check B: its sensitivity to room light (section 4.2, section 9).
- The VLM label helper: measure it on the 336 labelled places of r01-r07
  before relying on it.
- Optional speed-up: the classifier as a TensorRT engine; a smaller picture
  from the camera (section 10).
