#lang racket

; Import the command-line parser, readable output helpers, and YAML loader.
(require racket/cmdline
         racket/pretty
         yaml)

; Read the YAML program and require the root value to be a mapping.
(define (load-program path)
  (define program (call-with-input-file path read-yaml))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

; Keep builtin arity errors uniform across the evaluator.
(define (expect-arity who arguments count)
  (unless (= (length arguments) count)
    (error 'helix "~a expects exactly ~a argument~a"
           who
           count
           (if (= count 1) "" "s"))))

; Fold numbers inside a value so add can accept either plain numbers or a list of numbers.
(define (sum-value value)
  (cond
    [(number? value) value]
    [(list? value) (apply + (map sum-value value))]
    [else (error 'helix "add expects numeric arguments")]))

; add evaluates every argument and returns their total.
(define (builtin-add arguments program)
  (apply + (map (lambda (argument) (sum-value (evaluate argument program))) arguments)))

; eval resolves one value and then evaluates the result as code.
(define (builtin-eval arguments program)
  (expect-arity "eval" arguments 1)
  (evaluate (evaluate (first arguments) program) program))

; list eagerly evaluates every argument and returns the collected values.
(define (builtin-list arguments program)
  (map (lambda (argument) (evaluate argument program)) arguments))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program)
  (expect-arity "show" arguments 1)
  (evaluate (first arguments) program))

; Builtins resolve like any other symbol, so strings never need special handling.
(define builtins
  (hash "add" builtin-add
        "eval" builtin-eval
        "list" builtin-list
        "show" builtin-show))

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (cond
    [(hash-has-key? builtins name) (hash-ref builtins name)]
    [(hash-has-key? program name) (hash-ref program name)]
    [else (error 'helix "failed to resolve ~s" name)]))

; Evaluate strings as symbols, lists as either calls or data, and everything else as itself.
(define (evaluate node program)
  (cond
    [(string? node) (resolve program node)]
    [(list? node) (evaluate-list node program)]
    [else node]))

; A list is a call when its first evaluated value is a builtin procedure.
; Otherwise it is just a data grouping of already-resolved values.
(define (evaluate-list items program)
  (when (empty? items)
    (error 'helix "cannot evaluate an empty vector"))
  (define actor (evaluate (first items) program))
  (define arguments (rest items))
  (if (procedure? actor)
      (actor arguments program)
      (if (empty? arguments)
          actor
          (cons actor (map (lambda (argument) (evaluate argument program)) arguments)))))

; Print the final value in a readable format.
(define (render-result result)
  (displayln "result:")
  (pretty-write result))

; Accept an optional path and default to the bundled demo program.
(define program-path
  (command-line
   #:program "helix.rkt"
   #:args ([path "helix_demo.yaml"])
   path))

; Run the demo and report clean user-facing errors.
(with-handlers ([exn:fail?
                 (lambda (exn)
                   (eprintf "error: ~a\n" (exn-message exn))
                   (exit 1))])
  (define program (load-program program-path))
  (define entrypoint
    (hash-ref program "eval"
              (lambda ()
                (error 'helix "program is missing an eval entrypoint"))))
  (render-result (evaluate entrypoint program)))
