#lang racket

(provide builtin?
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

; Require a value to be a VM-like mapping with a main entrypoint.
(define (expect-vm who value)
  (unless (hash? value)
    (error 'helix "~a expects a VM mapping argument" who))
  (unless (hash-has-key? value "main")
    (error 'helix "~a expects a VM with a main entrypoint" who))
  value)

; add evaluates exactly two arguments and returns their sum.
(define (builtin-add arguments program resolve evaluate run-vm)
  (expect-arity "add" arguments 2)
  (+ (expect-number "add" (evaluate (first arguments) program))
     (expect-number "add" (evaluate (second arguments) program))))

; eval resolves one value and then evaluates the result as code.
(define (builtin-eval arguments program resolve evaluate run-vm)
  (expect-arity "eval" arguments 1)
  (evaluate (evaluate (first arguments) program) program))

; list resolves one stored sequence and evaluates each form inside it in order.
(define (builtin-list arguments program resolve evaluate run-vm)
  (expect-arity "list" arguments 1)
  (define values (evaluate (first arguments) program))
  (unless (list? values)
    (error 'helix "list expects a sequence argument"))
  (map (lambda (value) (evaluate value program)) values))

; show evaluates one argument and returns it unchanged.
(define (builtin-show arguments program resolve evaluate run-vm)
  (expect-arity "show" arguments 1)
  (evaluate (first arguments) program))

; set treats its left operand as a field name, evaluates the right operand,
; stores the result in the current program, and returns the stored value.
(define (builtin-set arguments program resolve evaluate run-vm)
  (expect-arity "set" arguments 2)
  (define field-name (expect-string "set" (first arguments)))
  (define value (evaluate (second arguments) program))
  (hash-set! program field-name value)
  value)

; start resolves a named nested VM and runs it through the VM executor.
(define (builtin-start arguments program resolve evaluate run-vm)
  (expect-arity "start" arguments 1)
  (define vm-name (expect-string "start" (first arguments)))
  (define vm (expect-vm "start" (resolve program vm-name)))
  (if run-vm
      (run-vm vm)
      (evaluate (hash-ref vm "main") vm)))

; Builtins resolve like any other symbol, so strings never need special handling.
(define builtins
  (hash "add" builtin-add
        "eval" builtin-eval
        "list" builtin-list
        "set" builtin-set
        "start" builtin-start
        "show" builtin-show))

; Builtin names can be recognized without coupling the evaluator to their bodies.
(define (builtin? name)
  (hash-has-key? builtins name))

; Resolve a builtin name into a procedure bound to the evaluator callbacks.
(define (resolve-builtin name resolve evaluate [run-vm #f])
  (define builtin
    (hash-ref builtins name #f))
  (and builtin
       (lambda (arguments program)
         (builtin arguments program resolve evaluate run-vm))))
