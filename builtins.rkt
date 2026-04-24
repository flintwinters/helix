#lang racket

(require racket/string
         yaml)

(provide builtin?
         call-with-step-frame
         call-with-step-runtime
         cells->yaml-string
         display-cells
         expect-arity
         expect-string
         resolve-builtin)

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

; The evaluator installs stepping callbacks only while a VM is being advanced.
(define current-step-runtime (make-parameter #f))

(define current-step-frame (make-parameter #f))

(define (call-with-step-runtime runtime thunk)
  (parameterize ([current-step-runtime runtime])
    (thunk)))

(define (call-with-step-frame frame thunk)
  (parameterize ([current-step-frame frame])
    (thunk)))

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

; Display cells in the standard VM-oriented YAML format.
(define (display-cells value)
  (displayln "\n---\nvm:")
  (display (cells->yaml-string value)))

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

; add evaluates exactly two arguments and returns their sum.
(define (builtin-add arguments program resolve evaluate)
  (expect-arity "add" arguments 2)
  (+ (expect-number "add" (evaluate (first arguments) program))
     (expect-number "add" (evaluate (second arguments) program))))

; eval resolves one value and then evaluates the result as code.
(define (builtin-eval arguments program resolve evaluate)
  (expect-arity "eval" arguments 1)
  (evaluate (evaluate (first arguments) program) program))

; list resolves one stored sequence and evaluates each form inside it in order.
(define (builtin-list arguments program resolve evaluate)
  (expect-arity "list" arguments 1)
  (define runtime (current-step-runtime))
  (define frame (current-step-frame))
  (define list-frame
    (and runtime
         (hash? frame)
         (equal? (hash-ref frame "name" #f) "list")
         frame))
  ; A resumed frame is only for this builtin invocation. Nested evaluation must
  ; not inherit it and accidentally treat it as its own resume state.
  (when list-frame
    (current-step-frame #f))
  (define values
    (frame-ref
     list-frame
     "values"
     (let ([resolved-values (evaluate (first arguments) program)])
       (unless (list? resolved-values)
         (error 'helix "list expects a sequence argument"))
       resolved-values)))
  (define (step-loop index)
    (let loop ([index index])
      (if (= index (length values))
          'null
          (let* ([value (evaluate (list-ref values index) program)]
                 [next-index (add1 index)])
            (if (= next-index (length values))
                'null
                (begin
                  ((hash-ref runtime 'yield!)
                   (make-hash
                    (list (cons "name" "list")
                          (cons "arguments" arguments)
                          (cons "values" values)
                          (cons "index" next-index))))
                  (void)))))))
  (if (not runtime)
      (begin
        (for-each (lambda (value) (evaluate value program)) values)
        'null)
      (step-loop (frame-ref list-frame "index" 0))))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program resolve evaluate)
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
(define (builtin-set arguments program resolve evaluate)
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

; Builtin names can be recognized without coupling the evaluator to their bodies.
(define (builtin? name) (hash-has-key? builtins name))

; Resolve a builtin name into a procedure bound to the evaluator callbacks.
(define (resolve-builtin name resolve evaluate)
  (define builtin
    (hash-ref builtins name #f))
  (and builtin
       (lambda (arguments program)
         (builtin arguments program resolve evaluate))))
