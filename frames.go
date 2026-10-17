package main

import (
	"encoding/json"
	"fmt"
	"image"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/leejianrong/primitive-pictures/primitive"
	"github.com/nfnt/resize"
)

// frameItem is one entry in <out>/frames.json, the per-frame record the
// pipeline's video-run manifest nests (SLICES-phase3.md V6/V7).
type frameItem struct {
	Index   int    `json:"index"`
	Source  string `json:"source"`
	Status  string `json:"status"` // "ok" or "resumed" (skipped, already on disk)
	Output  string `json:"output"`
	Shapes  int    `json:"shapes,omitempty"`
	Carried int    `json:"carried,omitempty"`
	Dropped int    `json:"dropped,omitempty"`
	// CarriedFraction/AddedFraction are the shape-continuity metric (ADR-0010)
	// for this frame against the previous one: the share of its shapes that
	// are inherited vs new. Observability only.
	CarriedFraction float64 `json:"carried_fraction"`
	AddedFraction   float64 `json:"added_fraction"`
	Added           int     `json:"added,omitempty"`
	Seconds         float64 `json:"seconds,omitempty"`
}

// listFrames returns the image files in dir, in lexical order (frames are
// zero-padded by the generator, so lexical is temporal).
func listFrames(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var paths []string
	for _, e := range entries {
		switch strings.ToLower(filepath.Ext(e.Name())) {
		case ".png", ".jpg", ".jpeg":
			if !e.IsDir() {
				paths = append(paths, filepath.Join(dir, e.Name()))
			}
		}
	}
	sort.Strings(paths)
	if len(paths) == 0 {
		return nil, fmt.Errorf("no .png/.jpg frames in %s", dir)
	}
	return paths, nil
}

func outStem(out, src string) string {
	return filepath.Join(out, strings.TrimSuffix(filepath.Base(src), filepath.Ext(src)))
}

// writeFileAtomic writes via a temp file + rename, so a killed run never
// leaves a half-written file that --resume would count as done.
func writeFileAtomic(path string, write func(tmp string) error) error {
	tmp := path + ".tmp"
	if err := write(tmp); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

func writeManifest(out string, items []frameItem) error {
	return writeFileAtomic(filepath.Join(out, "frames.json"), func(tmp string) error {
		b, err := json.MarshalIndent(items, "", "  ")
		if err != nil {
			return err
		}
		return os.WriteFile(tmp, append(b, '\n'), 0o644)
	})
}

// runFrames is the --frames mode: one in-process pass over the whole
// sequence (ADR-0007), writing each frame's SVG then PNG as it completes.
func runFrames(framesDir, out string, initial, perFrame int, dropRate float64, resume bool) error {
	if perFrame < 0 { // default: replace exactly what the drop rate removes, so the count holds steady
		perFrame = int(dropRate*float64(initial) + 0.5)
	}
	paths, err := listFrames(framesDir)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}

	// --resume skips the leading run of frames whose PNG (written last) is
	// already on disk. Shapes aren't serialized (ADR-0007), so the first
	// remaining frame starts cold instead of carrying the skipped frame's
	// shapes: one visible seam in exchange for not re-doing finished work.
	skip := 0
	var items []frameItem
	if resume {
		for skip < len(paths) {
			if _, err := os.Stat(outStem(out, paths[skip]) + ".png"); err != nil {
				break
			}
			items = append(items, frameItem{Index: skip, Source: paths[skip], Status: "resumed", Output: outStem(out, paths[skip]) + ".png"})
			skip++
		}
		if skip > 0 {
			primitive.Log(1, "resume: skipping %d completed frame(s)\n", skip)
		}
	}
	todo := paths[skip:]
	if len(todo) == 0 {
		return writeManifest(out, items)
	}

	load := func(i int) (image.Image, error) {
		im, err := primitive.LoadImage(todo[i])
		if err != nil {
			return nil, err
		}
		if InputSize > 0 {
			im = resize.Thumbnail(uint(InputSize), uint(InputSize), im, resize.Bilinear)
		}
		return im, nil
	}

	var bg primitive.Color
	if Background == "" {
		first, err := load(0)
		if err != nil {
			return err
		}
		bg = primitive.MakeColor(primitive.AverageImageColor(first))
	} else {
		bg = primitive.MakeHexColor(Background)
	}

	cfg := primitive.SequenceConfig{
		Size:              OutputSize,
		Workers:           Workers,
		Background:        bg,
		ShapeType:         primitive.ShapeType(Configs[0].Mode),
		Alpha:             Configs[0].Alpha,
		Repeat:            Configs[0].Repeat,
		InitialShapes:     initial,
		NewShapesPerFrame: perFrame,
		DropRate:          dropRate,
		StippleGrid:       Grid,
		StippleJitter:     Jitter,
	}
	err = primitive.ProcessSequence(len(todo), load, cfg, func(r primitive.FrameResult) error {
		idx := skip + r.Index
		stem := outStem(out, paths[idx])
		if err := writeFileAtomic(stem+".svg", func(tmp string) error {
			return primitive.SaveFile(tmp, r.Model.SVG())
		}); err != nil {
			return err
		}
		if err := writeFileAtomic(stem+".png", func(tmp string) error {
			return primitive.SavePNG(tmp, r.Model.Context.Image())
		}); err != nil {
			return err
		}
		items = append(items, frameItem{
			Index: idx, Source: paths[idx], Status: "ok", Output: stem + ".png",
			Shapes: len(r.Model.Shapes), Carried: r.Carried, Dropped: r.Dropped, Added: r.Added,
			CarriedFraction: r.Continuity.CarriedFraction(), AddedFraction: r.Continuity.AddedFraction(), Seconds: r.Elapsed.Seconds(),
		})
		primitive.Log(1, "frame %d/%d: shapes=%d carried=%d dropped=%d added=%d carried=%.0f%% score=%.6f (%.1fs)\n",
			idx+1, len(paths), len(r.Model.Shapes), r.Carried, r.Dropped, r.Added,
			100*r.Continuity.CarriedFraction(), r.Model.Score, r.Elapsed.Seconds())
		return writeManifest(out, items)
	})
	if err != nil {
		return err
	}
	printContinuitySummary(items)
	return writeManifest(out, items)
}

// printContinuitySummary reports the run-level continuity (always, not just
// with -v) so the effect of -new-shapes-per-frame/-drop-rate is visible
// without reading frames.json. Frames that start a run cold are excluded.
func printContinuitySummary(items []frameItem) {
	var sum float64
	warm := 0
	for _, it := range items {
		if it.Status == "ok" && it.Carried > 0 {
			sum += it.CarriedFraction
			warm++
		}
	}
	if warm == 0 {
		return
	}
	fmt.Fprintf(os.Stderr, "continuity: on average %.1f%% of each frame's shapes carried over from the previous frame (%d warm frames)\n",
		100*sum/float64(warm), warm)
}
