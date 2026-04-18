#lang racket

(require racket/cmdline
         racket/match
         racket/pretty
         yaml)

(define builtins (set "find" "eval" "list" "show"))

(define (load-program path)
  (define program (call-with-input-file path read-yaml))
  (unless (hash? program)
    (error 'helix "top-level YAML document must be a mapping"))
  program)

(define (resolve program name)
  (cond
    [(set-member? builtins name) name]
    [(hash-has-key? program name) (hash-ref program name)]
    [else (error 'helix "failed to resolve ~s" name)]))

(define (evaluate node program)
  (cond
    [(list? node) (evaluate-vector node program)]
    [(string? node) (resolve program node)]
    [else node]))

(define (expect-arity who arguments count)
  (unless (= (length arguments) count)
    (error 'helix "~a expects exactly ~a argument~a"
           who
           count
           (if (= count 1) "" "s"))))

(define (evaluate-vector items program)
  (match items
    ['() (error 'helix "cannot evaluate an empty vector")]
    [(list* actor-node arguments)
     (define actor (evaluate actor-node program))
     (cond
       [(equal? actor "find")
        (expect-arity "find" arguments 1)
        (define name (first arguments))
        (unless (string? name)
          (error 'helix "find expects exactly one string argument"))
        (resolve program name)]
       [(equal? actor "eval")
        (expect-arity "eval" arguments 1)
        (evaluate (evaluate (first arguments) program) program)]
       [(equal? actor "list")
        (map (lambda (argument) (evaluate argument program)) arguments)]
       [(equal? actor "show")
        (expect-arity "show" arguments 1)
        (evaluate (first arguments) program)]
       [else
        (error 'helix "vector actor did not resolve to a builtin")])]))

(define (render-result result)
  (displayln "result:")
  (pretty-write result))

(define program-path
  (command-line
   #:program "helix.rkt"
   #:args ([path "helix_demo.yaml"])
   path))

(with-handlers ([exn:fail?
                 (lambda (exn)
                   (eprintf "error: ~a\n" (exn-message exn))
                   (exit 1))])
  (define program (load-program program-path))
  (define entrypoint (hash-ref program "eval"
                               (lambda ()
                                 (error 'helix "program is missing an eval entrypoint"))))
  (render-result (evaluate entrypoint program)))
