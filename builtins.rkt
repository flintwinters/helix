#lang racket

(require racket/string
         yaml)

(provide builtin?
         call-with-resolve
         call-with-step-frame
         call-with-step-runtime
         cells->yaml-string
         evaluate
         expect-arity
         expect-string
         resolve-builtin)

; Keep builtin arity errors uniform across the evaluator.
(define (expect-arity who arguments count)
  (unless (= (length arguments) count)
    (error 'helix "~a expects exactly ~a argument~a"
           who count (if (= count 1) "" "s"))))

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

; The evaluator installs stepping callbacks only while a VM is being advanced.
(define current-resolve (make-parameter #f))

(define current-step-runtime (make-parameter #f))

(define current-step-frame (make-parameter #f))

(define (call-with-step-parameter parameter value thunk)
  (parameterize ([parameter value])
    (thunk)))

(define (call-with-resolve resolve thunk)
  (call-with-step-parameter current-resolve resolve thunk))

(define (call-with-step-runtime runtime thunk)
  (call-with-step-parameter current-step-runtime runtime thunk))

(define (call-with-step-frame frame thunk)
  (call-with-step-parameter current-step-frame frame thunk))

(define (frame-ref frame key default)
  (if frame
      (hash-ref frame key)
      default))

; Render cells as YAML without leaking host-language reference details.
(define (cells->yaml-string value)
  (define frame-paths
    (make-hasheq))
  (define empty-frame-placeholders
    (make-hasheq))
  (define next-empty-frame-id
    0)
  (define (empty-frame-placeholder entry)
    (hash-ref! empty-frame-placeholders
               entry
               (lambda ()
                 (set! next-empty-frame-id (add1 next-empty-frame-id))
                 (format "__helix_empty_frames_~a__" next-empty-frame-id))))
  (define (path->reference path)
    (string-join path "."))
  (define (render value path)
    (cond
      [(procedure? value) "<procedure>"]
      [(hash? value)
       (for/fold ([rendered (make-hash)])
                 ([key (sort (hash-keys value) string<?)])
         (define next-path
           (append path (list key)))
         (define entry
           (hash-ref value key))
         (define rendered-entry
           (cond
             [(and (string=? key "frames")
                   (box? entry)
                   (hash-has-key? frame-paths entry))
              (path->reference (hash-ref frame-paths entry))]
             [else
              (begin
                (when (and (string=? key "frames")
                           (box? entry))
                  (hash-set! frame-paths entry next-path))
                (if (and (string=? key "frames")
                         (box? entry)
                         (empty? (unbox entry)))
                    (empty-frame-placeholder entry)
                    (render entry next-path)))]))
         (hash-set! rendered key rendered-entry)
         rendered)]
      [(box? value)
       (render (unbox value) path)]
      [(list? value)
       (for/list ([entry value]
                  [index (in-naturals)])
         (render entry (append path (list (number->string index)))))]
      [else value]))
  (define output
    (with-output-to-string
      (lambda ()
        (write-yaml (render value '())))))
  (regexp-replace* #rx"__helix_empty_frames_[0-9]+__"
                   output
                   "[]"))

; Append one printed line to stdout only when the VM already owns that field.
(define (append-stdout! who program rendered-value)
  (when (hash-has-key? program "stdout")
    (define current-stdout
      (hash-ref program "stdout"))
    (unless (string? current-stdout)
      (error 'helix "~a expects stdout to be a string when present" who))
    (hash-set! program
               "stdout"
               (string-append current-stdout rendered-value "\n"))))

; Evaluate a sequence in order, optionally yielding between elements.
(define (evaluate-sequence who arguments values program)
  (define runtime (current-step-runtime))
  (define frame (current-step-frame))
  (define sequence-frame
    (and runtime
         (hash? frame)
         (equal? (hash-ref frame "name" #f) who)
         frame))
  ; A resumed frame is only for this builtin invocation. Nested evaluation must
  ; not inherit it and accidentally treat it as its own resume state.
  (when sequence-frame
    (current-step-frame #f))
  (define start-index
    (frame-ref sequence-frame "index" 0))
  (define (maybe-yield! next-index)
    (when runtime
      ((hash-ref runtime 'yield!)
       (make-hash
        (list (cons "name" who)
              (cons "arguments" arguments)
              (cons "values" values)
              (cons "index" next-index))))))
  (let loop ([index start-index])
    (if (= index (length values))
        'null
        (let ([next-index (add1 index)])
          (evaluate (list-ref values index) program)
          (if (= next-index (length values))
              'null
              (begin
                (maybe-yield! next-index)
                (loop next-index)))))))

; Preserve direct recursive evaluation for resolved builtin procedures used as values.
(define (evaluate node program)
  (define resolve
    (current-resolve))
  (unless resolve
    (error 'helix "evaluate requires an active resolver"))
  (cond
    [(string? node) (resolve program node)]
    [(list? node) (evaluate-list node program)]
    [else node]))

(define (evaluate-list items program)
  (when (empty? items)
    (error 'helix "cannot evaluate an empty vector"))
  (define actor
    (evaluate (first items) program))
  (unless (procedure? actor)
    (error 'helix "vector actor did not resolve to a builtin"))
  (actor (rest items) program))

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
  (define values
    (let ([frame (current-step-frame)])
      (frame-ref
       (and (current-step-runtime)
            (hash? frame)
            (equal? (hash-ref frame "name" #f) "list")
            frame)
       "values"
       (let ([resolved-values (evaluate (first arguments) program)])
         (unless (list? resolved-values)
           (error 'helix "list expects a sequence argument"))
         resolved-values))))
  (evaluate-sequence "list" arguments values program))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program)
  (expect-arity "show" arguments 1)
  (define value
    (evaluate (first arguments) program))
  (define rendered-value
    (string-trim (cells->yaml-string value) "\n" #:repeat? #t))
  (displayln rendered-value)
  (append-stdout! "show" program rendered-value)
  value)

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
  (hash "add"  builtin-add
        "eval" builtin-eval
        "list" builtin-list
        "set"  builtin-set
        "show" builtin-show))

; Builtin names can be recognized without coupling the evaluator to their bodies.
(define (builtin? name) (hash-has-key? builtins name))

; Resolve a builtin name into its callable procedure.
(define (resolve-builtin name)
  (hash-ref builtins name #f))
