# Memory Management

Helix uses a split ownership model based on whether a `Cell` represents a
successful value or an error. The `alive` field is the discriminator. When
`alive` is `true`, the cell is a borrowed success value. When `alive` is
`false`, the cell is an owned error value.

Successful cells are never freed by the caller that receives them. They are
borrowed observations of existing runtime state. A function may inspect or
return a successful cell, but it must not assume ownership of it and must not
destroy it.

Errored cells follow a different rule. An errored cell is owned by the current
frame that receives it. That frame has exactly two valid actions: it may handle
the error locally and free it, or it may return the error upward unchanged. In
the second case, ownership transfers monotonically to the caller. Once a frame
returns an owned error, it must treat that error as no longer belonging to it.

This monotonic transfer rule is the core invariant. Error ownership must only
move upward through the call chain until some frame becomes the terminal
consumer. In ordinary execution, the terminal consumer is often `main`, which
prints the error and then frees it. No frame may duplicate ownership of an
error, and no frame may free an error after transferring it upward.

Error containers are permitted. A frame may assemble multiple owned errors into
a larger error cell, provided that the child errors are incorporated into that
new container in a monotonic way. Once wrapped, the container becomes the owned
error object that is either handled locally or returned upward. The same child
error must not be shared across multiple owners or reused after transfer.

Under this model, `alive` does not describe truthiness. It describes whether the
cell is a successful borrowed value or an owned error value. Truthiness is a
separate value-level property of `Cell` and must be evaluated independently.
