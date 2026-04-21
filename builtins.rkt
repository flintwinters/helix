#lang racket

(provide builtin?
         expect-arity
         expect-string
         resolve-builtin
         resume-builtin-frame!)

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

; Runtime callbacks let builtin frames drive the shared step engine.
(define (runtime-ref runtime key)
  (hash-ref runtime key
            (lambda ()
              (error 'helix "missing runtime callback: ~a" key))))

(define (runtime-call runtime key . arguments)
  (apply (runtime-ref runtime key) arguments))

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
  (define values (evaluate (first arguments) program))
  (unless (list? values)
    (error 'helix "list expects a sequence argument"))
  (map (lambda (value) (evaluate value program)) values))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program resolve evaluate)
  (expect-arity "show" arguments 1)
  (evaluate (first arguments) program))

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
(define (builtin? name)
  (hash-has-key? builtins name))

; Resolve a builtin name into a procedure bound to the evaluator callbacks.
(define (resolve-builtin name resolve evaluate)
  (define builtin
    (hash-ref builtins name #f))
  (and builtin
       (lambda (arguments program)
         (builtin arguments program resolve evaluate))))

; Resume one builtin-owned continuation frame inside the shared evaluator.
(define (resume-builtin-frame! name frame runtime)
  (define resolve
    (runtime-ref runtime 'resolve))
  (define remember-vm-source!
    (runtime-ref runtime 'remember-vm-source!))
  (define vm-base-directory
    (runtime-ref runtime 'vm-base-directory))
  (define run-vm!
    (runtime-ref runtime 'run-vm!))
  (case (string->symbol name)
    [(add)
     (define arguments
       (hash-ref frame "arguments"))
     (expect-arity "add" arguments 2)
     (define phase
       (hash-ref frame "phase" "enter"))
     (cond
       [(equal? phase "enter")
        (hash-set! frame "phase" "right")
        (runtime-call runtime 'push-expr! (first arguments) (hash-ref frame "program"))]
       [(equal? phase "right")
        (define left
          (expect-number "add" (runtime-call runtime 'take-value!)))
        (hash-set! frame "left" left)
        (hash-set! frame "phase" "finish")
        (runtime-call runtime 'push-expr! (second arguments) (hash-ref frame "program"))]
       [else
        (define right
          (expect-number "add" (runtime-call runtime 'take-value!)))
        (runtime-call runtime 'complete-frame! (+ (hash-ref frame "left") right))])]
    [(eval)
     (define arguments
       (hash-ref frame "arguments"))
     (expect-arity "eval" arguments 1)
     (define phase
       (hash-ref frame "phase" "enter"))
     (cond
       [(equal? phase "enter")
        (hash-set! frame "phase" "finish")
        (runtime-call runtime 'push-expr! (first arguments) (hash-ref frame "program"))]
       [(equal? phase "finish")
        (define next-node
          (runtime-call runtime 'take-value!))
        (hash-set! frame "phase" "return")
        (runtime-call runtime 'push-expr! next-node (hash-ref frame "program"))]
       [else
        (runtime-call runtime 'complete-frame! (runtime-call runtime 'take-value!))])]
    [(list)
     (define arguments
       (hash-ref frame "arguments"))
     (expect-arity "list" arguments 1)
     (define phase
       (hash-ref frame "phase" "enter"))
     (cond
       [(equal? phase "enter")
        (hash-set! frame "phase" "values")
        (runtime-call runtime 'push-expr! (first arguments) (hash-ref frame "program"))]
       [(equal? phase "values")
        (define values
          (runtime-call runtime 'take-value!))
        (unless (list? values)
          (error 'helix "list expects a sequence argument"))
        (hash-set! frame "values" values)
        (hash-set! frame "results" '())
        (hash-set! frame "index" 0)
        (if (empty? values)
            (runtime-call runtime 'complete-frame! '())
            (begin
              (hash-set! frame "phase" "element")
              (runtime-call runtime 'push-expr! (first values) (hash-ref frame "program"))))]
       [(equal? phase "element")
        (define results
          (append (hash-ref frame "results")
                  (list (runtime-call runtime 'take-value!))))
        (define next-index
          (add1 (hash-ref frame "index")))
        (define values
          (hash-ref frame "values"))
        (hash-set! frame "results" results)
        (hash-set! frame "index" next-index)
        (if (= next-index (length values))
            (runtime-call runtime 'complete-frame! results)
            (begin
              (hash-set! frame "phase" "resume")
              (runtime-call runtime 'yield!)))]
       [else
        (define values
          (hash-ref frame "values"))
        (define next-index
          (hash-ref frame "index"))
        (hash-set! frame "phase" "element")
        (runtime-call runtime 'push-expr! (list-ref values next-index) (hash-ref frame "program"))])]
    [(set)
     (define arguments
       (hash-ref frame "arguments"))
     (expect-arity "set" arguments 2)
     (define field-name
       (expect-string "set" (first arguments)))
     (define phase
       (hash-ref frame "phase" "enter"))
     (cond
       [(equal? phase "enter")
        (hash-set! frame "field-name" field-name)
        (hash-set! frame "phase" "finish")
        (runtime-call runtime 'push-expr! (second arguments) (hash-ref frame "program"))]
       [else
        (define value
          (runtime-call runtime 'take-value!))
        (hash-set! (hash-ref frame "program") (hash-ref frame "field-name") value)
        (runtime-call runtime 'complete-frame! value)])]
    [(show)
     (define arguments
       (hash-ref frame "arguments"))
     (expect-arity "show" arguments 1)
     (define phase
       (hash-ref frame "phase" "enter"))
     (cond
       [(equal? phase "enter")
        (hash-set! frame "phase" "finish")
        (runtime-call runtime 'push-expr! (first arguments) (hash-ref frame "program"))]
       [else
        (runtime-call runtime 'complete-frame! (runtime-call runtime 'take-value!))])]
    [else
     (error 'helix "failed to resume builtin frame ~s" name)]))
