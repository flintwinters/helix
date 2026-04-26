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

; Ensure every runnable VM has a mutable state mapping.
(define (ensure-vm-state! vm)
  (define existing
    (hash-ref vm "state" #f))
  (define state
    (if (hash? existing)
        existing
        (let ([fresh (make-hash)])
          (hash-set! vm "state" fresh)
          fresh)))
  (unless (hash-has-key? state "status")
    (hash-set! state "status" "ready"))
  (unless (hash-has-key? state "frames")
    (hash-set! state "frames" (box '())))
  (unless (box? (hash-ref state "frames"))
    (hash-set! state "frames" (box (hash-ref state "frames"))))
  state)

; Remove stale transient fields before a VM starts or restarts execution.
(define (reset-vm-state! state)
  (hash-set! state "frames" '())
  (when (hash-has-key? state "result")
    (hash-remove! state "result"))
  (when (hash-has-key? state "error")
    (hash-remove! state "error")))

; Prepare a fresh or resumed VM for execution.
(define (initialize-vm! vm)
  (define state
    (ensure-vm-state! vm))
  (when (equal? (hash-ref state "status") "ready")
    (apply-includes! vm)
    (reset-vm-state! state)
    (hash-ref vm "main"
              (lambda ()
                (error 'helix "program is missing a main entrypoint")))
    (hash-set! state "status" "running"))
  state)

; Report execution failures through VM state before surfacing them normally.
(define (mark-vm-error! vm message)
  (define state
    (ensure-vm-state! vm))
  (hash-set! state "status" "error")
  (hash-set! state "error" message))

; Access and update the VM frame stack through one set of helpers.
(define (vm-frames vm)
  (unbox (hash-ref (ensure-vm-state! vm) "frames")))

(define (set-vm-frames! vm frames)
  (set-box! (hash-ref (ensure-vm-state! vm) "frames") frames))

(define (vm-status-result state)
  (case (string->symbol (hash-ref state "status"))
    [(finished) (hash-ref state "result")]
    [(error) (error 'helix "~a" (hash-ref state "error"))]
    [else #f]))

(define (finish-vm! state result)
  (hash-set! state "status" "finished")
  (hash-set! state "result" result)
  result)

(define (with-vm-running-state vm continue)
  (define state
    (ensure-vm-state! vm))
  (or (vm-status-result state)
      (continue state)))

(define (resolve-child-vm who arguments program)
  (expect-arity who arguments 1)
  (define vm
    (expect-vm who
               (resolve program
                        (expect-string who (first arguments)))))
  (remember-vm-source! vm (vm-base-directory program))
  vm)

(define (with-resolved-child-vm who arguments program continue)
  (continue (resolve-child-vm who arguments program)))

; Run or resume a VM until it yields once or reaches a terminal state.
(define (advance-vm! vm)
  (expect-vm "advance-vm!" vm)
  (with-handlers ([exn:fail?
                   (lambda (exn)
                     (mark-vm-error! vm (exn-message exn))
                     (raise exn))])
    (initialize-vm! vm)
    (with-vm-running-state
     vm
     (lambda (state)
       (let ([runtime (hasheq 'yield! (lambda (frame)
                                        (set-vm-frames! vm (list frame))))])
         (define (resume-or-start)
           (if (empty? (vm-frames vm))
               (call-with-step-frame
                #f
                (lambda ()
                  (evaluate (hash-ref vm "main") vm)))
               (let ([frame (first (vm-frames vm))])
                 (set-vm-frames! vm '())
                 (call-with-step-frame
                  frame
                  (lambda ()
                    (evaluate
                     (cons (hash-ref frame "name")
                           (hash-ref frame "arguments"))
                     vm))))))
         (define result
           (call-with-resolve
            resolve
            (lambda ()
              (call-with-step-runtime runtime resume-or-start))))
         (if (empty? (vm-frames vm))
             (finish-vm! state result)
             vm))))))

; Keep running shared steps until the target VM reaches a terminal state.
(define (run-vm vm)
  (expect-vm "run-vm" vm)
  (let loop ()
    (with-vm-running-state
     vm
     (lambda (_state)
       (advance-vm! vm)
       (loop)))))

; start resolves a named nested VM and runs it through the shared scheduler.
(define (builtin-start arguments program)
  (with-resolved-child-vm
   "start"
   arguments
   program
   run-vm))

; step resolves a named nested VM and advances it once through the same scheduler.
(define (builtin-step arguments program)
  (with-resolved-child-vm
   "step"
   arguments
   program
   (lambda (vm)
     (advance-vm! vm)
     (hash-ref (ensure-vm-state! vm) "status"))))

; Runtime-owned builtins share the same dispatch path as imported builtins.
(define local-builtins
  (hash "start" builtin-start
        "step" builtin-step))

; Resolve a symbol name to either a builtin procedure or a program value.
(define (resolve program name)
  (define builtin
    (or (hash-ref local-builtins name #f)
        (resolve-builtin name)))
  (cond
    [builtin builtin]
    [(hash-has-key? program name) (hash-ref program name)]
    [(string-contains? name ".")
     (resolve-path program (string-split name ".") name)]
    [else (error 'helix "failed to resolve ~s" name)]))

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
  (display (cells->yaml-string program)))
