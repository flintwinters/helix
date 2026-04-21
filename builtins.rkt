#lang racket

(provide builtin?
         call-with-step-runtime
         expect-arity
         expect-string
         resume-builtin-frame!
         resolve-builtin
         )

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
(define current-step-runtime
  (make-parameter #f))

(define (call-with-step-runtime runtime thunk)
  (parameterize ([current-step-runtime runtime])
    (thunk)))

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
  (define runtime
    (current-step-runtime))
  (if (not runtime)
      (map (lambda (value) (evaluate value program)) values)
      (let loop ([remaining values]
                 [results '()])
        (if (empty? remaining)
            (reverse results)
            (let* ([value (evaluate (first remaining) program)]
                   [next-results (cons value results)]
                   [rest-values (rest remaining)])
              (if (empty? rest-values)
                  (reverse next-results)
                  (let ([resumed?
                         (call-with-current-continuation
                          (lambda (resume-k)
                            ((hash-ref runtime 'yield!)
                             (make-hash
                              (list (cons "name" "list")
                                    (cons "resume"
                                          (lambda ()
                                            (resume-k #t))))))
                            #f))])
                    (if resumed?
                        (loop rest-values next-results)
                        (void)))))))))

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

; Resume whatever continuation frame a builtin stored on the VM.
(define (resume-builtin-frame! frame)
  (define resume
    (hash-ref frame "resume" #f))
  (unless (procedure? resume)
    (error 'helix "builtin frame is missing a resume continuation"))
  (resume))
