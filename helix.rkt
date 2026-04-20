#lang racket

; Import the command-line parser, readable output helpers, and YAML loader.
(require racket/cmdline
         racket/path
         racket/string
         "builtins.rkt"
         yaml)

; Track the base directory associated with each loaded VM mapping.
(define vm-base-directories
  (make-hasheq))

; Associate a VM mapping with the directory its YAML file came from.
(define (remember-vm-source! value base-dir)
  (when (hash? value)
    (hash-set! vm-base-directories value base-dir))
  value)

; Resolve the base directory for a VM, falling back to the current directory.
(define (vm-base-directory vm)
  (hash-ref vm-base-directories vm (current-directory)))

; Read an arbitrary YAML value and associate its nested mappings with its source.
(define (load-yaml-file path)
  (define full-path
    (simplify-path (path->complete-path path)))
  (define value
    (call-with-input-file full-path read-yaml))
  (define base-dir
    (or (path-only full-path) (current-directory)))
  (remember-vm-source! value base-dir))

; Read the YAML program and require the root value to be a mapping.
(define (load-program path)
  (define program (load-yaml-file path))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

; Resolve an include path, load its YAML value, and derive the inserted key.
(define (load-include-entry base-dir include-path)
  (unless (string? include-path)
    (error 'helix "include expects string file paths"))
  (define candidate
    (string->path include-path))
  (define full-path
    (simplify-path
     (if (relative-path? candidate)
         (build-path base-dir candidate)
         candidate)))
  (define key
    (regexp-replace #rx"\\.[^.]+$"
                    (path->string (file-name-from-path full-path))
                    ""))
  (values key (load-yaml-file full-path)))

; Load include files into the VM before its main entrypoint runs.
(define (apply-includes! vm)
  (when (hash-has-key? vm "include")
    (define include-field
      (hash-ref vm "include"))
    (define include-paths
      (cond
        [(string? include-field) (list include-field)]
        [(list? include-field) include-field]
        [else (error 'helix "include expects a string or sequence of strings")]))
    (define base-dir
      (vm-base-directory vm))
    (for ([include-path include-paths])
      (define-values (key value)
        (load-include-entry base-dir include-path))
      (hash-set! vm key value))))

; Require a value to be a VM-like mapping with a main entrypoint.
(define (expect-vm who value)
  (unless (hash? value)
    (error 'helix "~a expects a VM mapping argument" who))
  value)

; Follow a dot-delimited path through nested VM mappings.
(define (resolve-path current segments name)
  (cond
    [(empty? segments) current]
    [(not (hash? current)) (error 'helix "failed to resolve ~s" name)]
    [(hash-has-key? current (first segments))
     (resolve-path (hash-ref current (first segments))
                   (rest segments)
                   name)]
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

; Prepare a VM and execute its main entrypoint.
(define (run-vm vm)
  (expect-vm "run-vm" vm)
  (apply-includes! vm)
  (define entrypoint
    (hash-ref vm "main"
              (lambda ()
                (error 'helix "program is missing a main entrypoint"))))
  (evaluate entrypoint vm))

; start resolves a named nested VM and runs it through the centralized entrypoint.
(define (builtin-start arguments program)
  (expect-arity "start" arguments 1)
  (define vm-name
    (expect-string "start" (first arguments)))
  (define vm
    (expect-vm "start" (resolve program vm-name)))
  (remember-vm-source! vm (vm-base-directory program))
  (run-vm vm))

; Runtime-owned builtins share the same dispatch path as imported builtins.
(define local-builtins
  (hash "start" builtin-start))

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (define builtin
    (or (hash-ref local-builtins name #f)
        (resolve-builtin name resolve evaluate)))
  (cond
    [builtin builtin]
    [(hash-has-key? program name) (hash-ref program name)]
    [(string-contains? name ".")
     (resolve-path program (string-split name ".") name)]
    [else (error 'helix "failed to resolve ~s" name)]))

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
  (run-vm program)
  (render-vm-state program))
