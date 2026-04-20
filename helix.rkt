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

; Record a source directory for every mapping nested inside a loaded YAML value.
(define (register-vm-source! value base-dir)
  (cond
    [(hash? value)
     (hash-set! vm-base-directories value base-dir)
     (for ([entry (in-hash-values value)])
       (register-vm-source! entry base-dir))]
    [(list? value)
     (for ([entry value])
       (register-vm-source! entry base-dir))]
    [else (void)]))

; Read an arbitrary YAML value and associate its nested mappings with its source.
(define (load-yaml-file path)
  (define full-path
    (simplify-path (path->complete-path path)))
  (define value
    (call-with-input-file full-path read-yaml))
  (define base-dir
    (or (path-only full-path) (current-directory)))
  (register-vm-source! value base-dir)
  value)

; Read the YAML program and require the root value to be a mapping.
(define (load-program path)
  (define program (load-yaml-file path))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

; Normalize the include field into a sequence of file path strings.
(define (include-paths value)
  (cond
    [(string? value) (list value)]
    [(list? value)
     (for/list ([entry value])
       (unless (string? entry)
         (error 'helix "include expects string file paths"))
       entry)]
    [else (error 'helix "include expects a string or sequence of strings")]))

; Derive the inserted key from the included file's basename without extension.
(define (include-key path)
  (define raw-name
    (path->string (file-name-from-path path)))
  (regexp-replace #rx"\\.[^.]+$" raw-name ""))

; Resolve include paths relative to the VM source directory when needed.
(define (resolve-include-path base-dir include-path)
  (define candidate
    (string->path include-path))
  (simplify-path
   (if (relative-path? candidate)
       (build-path base-dir candidate)
       candidate)))

; Load include files into the VM before its main entrypoint runs.
(define (apply-includes! vm)
  (when (hash-has-key? vm "include")
    (define base-dir
      (hash-ref vm-base-directories vm (current-directory)))
    (for ([include-path (include-paths (hash-ref vm "include"))])
      (define full-path
        (resolve-include-path base-dir include-path))
      (hash-set! vm
                 (include-key full-path)
                 (load-yaml-file full-path)))))

; Require a value to be a VM-like mapping with a main entrypoint.
(define (expect-vm who value)
  (unless (hash? value)
    (error 'helix "~a expects a VM mapping argument" who))
  (unless (hash-has-key? value "main")
    (error 'helix "~a expects a VM with a main entrypoint" who))
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
  (apply-includes! vm)
  (define entrypoint
    (hash-ref vm "main"
              (lambda ()
                (error 'helix "program is missing a main entrypoint"))))
  (evaluate entrypoint vm))

; start resolves a named nested VM and runs it through the centralized entrypoint.
(define (builtin-start arguments program)
  (unless (= (length arguments) 1)
    (error 'helix "start expects exactly one argument"))
  (define vm-name (first arguments))
  (unless (string? vm-name)
    (error 'helix "start expects a string argument"))
  (run-vm (expect-vm "start" (resolve program vm-name))))

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (cond
    [(string=? name "start") builtin-start]
    [(resolve-builtin name resolve evaluate) => values]
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
