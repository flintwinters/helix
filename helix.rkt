#lang racket

; Import the small set of libraries used by the demo runner.
(require racket/cmdline
         racket/match
         racket/pretty
         yaml)

; Builtins live in their own namespace so program keys can reuse names like "eval".
(define builtins (set "add" "find" "eval" "list" "show"))

; Read a YAML document from disk and ensure the root value is a mapping.
(define (load-program path)
  (define program (call-with-input-file path read-yaml))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

; Resolve either a builtin name or a value stored at the root of the program.
(define (resolve program name)
  (cond
    [(set-member? builtins name) name]
    [(hash-has-key? program name) (hash-ref program name)]
    [else (error 'helix "failed to resolve ~s" name)]))

; Evaluate vectors as calls, strings as symbolic references, and everything else as data.
(define (evaluate node program)
  (cond
    [(list? node) (evaluate-vector node program)]
    [(string? node) (resolve program node)]
    [else node]))

; Keep builtin error messages consistent when they expect a fixed number of arguments.
(define (expect-arity who arguments count)
  (unless (= (length arguments) count)
    (error 'helix "~a expects exactly ~a argument~a"
           who
           count
           (if (= count 1) "" "s"))))

; Ensure arithmetic inputs are numbers before the builtin operates on them.
(define (expect-number who value)
  (unless (number? value)
    (error 'helix "~a expects numeric arguments" who))
  value)

; A vector is a call expression where the first item names the builtin to execute.
(define (evaluate-vector items program)
  (match items
    ['() (error 'helix "cannot evaluate an empty vector")]
    [(list* actor-node arguments)
     (define actor (evaluate actor-node program))
     (cond
       ; add evaluates each argument and returns their numeric sum.
       [(equal? actor "add")
        (apply +
               (map (lambda (argument)
                      (expect-number "add" (evaluate argument program)))
                    arguments))]
       ; find looks up a single symbolic name in the root program mapping.
       [(equal? actor "find")
        (expect-arity "find" arguments 1)
        (define name (first arguments))
        (unless (string? name)
          (error 'helix "find expects exactly one string argument"))
        (resolve program name)]
       ; eval resolves one value and then evaluates the result as code.
       [(equal? actor "eval")
        (expect-arity "eval" arguments 1)
        (evaluate (evaluate (first arguments) program) program)]
       ; list eagerly evaluates each argument and collects the results.
       [(equal? actor "list")
        (map (lambda (argument) (evaluate argument program)) arguments)]
       ; show simply returns its evaluated argument so demos can expose nested data.
       [(equal? actor "show")
        (expect-arity "show" arguments 1)
        (evaluate (first arguments) program)]
       [else
        (error 'helix "vector actor did not resolve to a builtin")])]))

; Print the evaluated result in a readable format.
(define (render-result result)
  (displayln "result:")
  (pretty-write result))

; Accept an optional YAML file path and default to the bundled demo program.
(define program-path
  (command-line
   #:program "helix.rkt"
   #:args ([path "helix_demo.yaml"])
   path))

; Report user-facing errors cleanly instead of showing a raw Racket stack trace.
(with-handlers ([exn:fail?
                 (lambda (exn)
                   (eprintf "error: ~a\n" (exn-message exn))
                   (exit 1))])
  ; Load the program and require it to define the standard "eval" entrypoint.
  (define program (load-program program-path))
  (define entrypoint (hash-ref program "eval"
                               (lambda ()
                                 (error 'helix "program is missing an eval entrypoint"))))
  ; Evaluate the entrypoint and print the final value.
  (render-result (evaluate entrypoint program)))
