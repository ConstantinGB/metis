package store

// Store keeps key/value pairs in memory.
type Store struct{ data map[string]string }

// New creates an empty Store.
func New() *Store { return &Store{data: map[string]string{}} }

func (s *Store) Get(k string) string { return s.data[k] }
