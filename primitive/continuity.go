package primitive

// Continuity compares two consecutive frames' shape lists (ADR-0010). A shape
// counts as carried when the very same Shape value appears in both lists —
// shapes are immutable once committed to a Model, and NewModelFrom reuses them
// as-is, so identity is exact where comparing fields would be fuzzy.
//
// It is descriptive only: nothing in the search reads it.
type Continuity struct {
	Prev    int // shapes in the previous frame
	Cur     int // shapes in the current frame
	Carried int // in both frames
	Added   int // only in the current frame
	Dropped int // only in the previous frame
}

// MeasureContinuity compares prev and cur. A nil prev (first frame) reports
// every shape as added.
func MeasureContinuity(prev, cur []Shape) Continuity {
	inPrev := make(map[Shape]struct{}, len(prev))
	for _, s := range prev {
		inPrev[s] = struct{}{}
	}
	c := Continuity{Prev: len(prev), Cur: len(cur)}
	for _, s := range cur {
		if _, ok := inPrev[s]; ok {
			c.Carried++
		} else {
			c.Added++
		}
	}
	c.Dropped = c.Prev - c.Carried
	return c
}

// CarriedFraction is the share of the current frame's shapes inherited
// unchanged; 0 for an empty frame.
func (c Continuity) CarriedFraction() float64 { return fraction(c.Carried, c.Cur) }

// AddedFraction is the share of the current frame's shapes that are new.
func (c Continuity) AddedFraction() float64 { return fraction(c.Added, c.Cur) }

func fraction(n, of int) float64 {
	if of == 0 {
		return 0
	}
	return float64(n) / float64(of)
}
