package primitive

import "fmt"

var LogLevel int

func Log(level int, format string, a ...interface{}) {
	if LogLevel >= level {
		fmt.Printf(format, a...)
	}
}

func vv(format string, a ...interface{}) {
	Log(2, "  "+format, a...)
}
