package main

import (
	"fmt"

	"example.com/gamma/internal/store"
)

func main() {
	s := store.New()
	fmt.Println(s.Get("k"))
}
