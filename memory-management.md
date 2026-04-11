# Memory Management

Helix uses a split ownership model based on whether a `Cell` represents a
successful value or an error. The `alive` field is the discriminator. When
`alive` is `true`, the cell is a borrowed success value. When `alive` is
`false`, the cell is an error value rooted by `Zygote["errors"]`.

Successful cells are never freed by the caller that receives them. They are
borrowed observations of existing runtime state. A function may inspect or
return a successful cell, but it must not assume ownership of it and must not
destroy it.

Errored cells are first-class values, but they are rooted separately from
ordinary successful runtime objects. Every new error created by `Error()` is
registered in the `errors` vector stored on `Zygote`. Callers therefore borrow
errored cells just as they borrow successful cells; they do not individually
free them during propagation.

The root ownership invariant is now simpler: successful heap cells are kept in a
private rooted-success store, while errored cells are kept in `Zygote["errors"]`
until shutdown. Evaluation frames pass both kinds of cells upward by borrowed
reference. The terminal path may print an error, but destruction of rooted
errors happens during `Zygote` teardown rather than at the print site.

Error containers are permitted. A frame may assemble multiple owned errors into
a larger error cell, provided that the child errors are incorporated into that
new container in a monotonic way. Once wrapped, the container becomes the
root-level error object that is returned upward. The same child error must not
be shared across multiple owners or reused after transfer.

Under this model, `alive` does not describe truthiness. It describes whether the
cell is a successful borrowed value or an owned error value. Truthiness is a
separate value-level property of `Cell` and must be evaluated independently.
