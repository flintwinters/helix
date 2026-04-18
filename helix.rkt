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

; Require a value to be numeric before arithmetic uses it.
(define (expect-number who value)
  (unless (number? value)
    (error 'helix "~a expects numeric arguments" who))
  value)

; Require a value to be a string before builtins treat it as a field name.
(define (expect-string who value)
  (unless (string? value)
    (error 'helix "~a expects a string argument" who))
  value)

; add evaluates exactly two arguments and returns their sum.
(define (builtin-add arguments program)
  (expect-arity "add" arguments 2)
  (+ (expect-number "add" (evaluate (first arguments) program))
     (expect-number "add" (evaluate (second arguments) program))))

; eval resolves one value and then evaluates the result as code.
(define (builtin-eval arguments program)
  (expect-arity "eval" arguments 1)
  (evaluate (evaluate (first arguments) program) program))

; list resolves one stored sequence and evaluates each form inside it in order.
(define (builtin-list arguments program)
  (expect-arity "list" arguments 1)
  (define values (evaluate (first arguments) program))
  (unless (list? values)
    (error 'helix "list expects a sequence argument"))
  (map (lambda (value) (evaluate value program)) values))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program)
  (expect-arity "show" arguments 1)
  (evaluate (first arguments) program))

; set treats its left operand as a field name, evaluates the right operand,
; stores the result in the current program, and returns the stored value.
(define (builtin-set arguments program)
  (expect-arity "set" arguments 2)
  (define field-name (expect-string "set" (first arguments)))
  (define value (evaluate (second arguments) program))
  (hash-set! program field-name value)
  value)

; Builtins resolve like any other symbol, so strings never need special handling.
(define builtins
  (hash "add" builtin-add
        "eval" builtin-eval
        "list" builtin-list
        "set" builtin-set
        "show" builtin-show))

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (cond
    [(hash-has-key? builtins name) (hash-ref builtins name)]
    [(hash-has-key? program name) (hash-ref program name)]
    [else (error 'helix "failed to resolve ~s" name)]))

; Evaluate strings as symbols, lists as calls, and everything else as itself.
(define (evaluate node program)
  (cond
    [(string? node) (resolve program node)]
    [(list? node) (evaluate-list node program)]
    [else node]))

; Evaluate only the actor position; builtins are responsible for their quoted arguments.
(define (evaluate-list items program)
  (when (empty? items)
    (error 'helix "cannot evaluate an empty vector"))
  (define actor (evaluate (first items) program))
  (unless (procedure? actor)
    (error 'helix "vector actor did not resolve to a builtin"))
  (actor (rest items) program))

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
    (hash-ref program "main"
              (lambda ()
                (error 'helix "program is missing a main entrypoint"))))
  (render-result (evaluate entrypoint program)))
