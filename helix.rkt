#lang racket

; Import the command-line parser, readable output helpers, and YAML loader.
(require racket/cmdline
         "builtins.rkt"
         yaml)

; Read the YAML program and require the root value to be a mapping.
(define (load-program path)
  (define program (call-with-input-file path read-yaml))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (define builtin
    (resolve-builtin name resolve evaluate))
  (cond
    [builtin builtin]
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

; Print the full VM state in a readable format after evaluation completes.
(define (render-vm-state program)
  (displayln "vm:")
  (write-yaml program))

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
  (evaluate entrypoint program)
  (render-vm-state program))
