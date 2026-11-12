# Load with: source <(helix completion bash)
_helix_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}" command="${COMP_WORDS[1]}"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W '@COMMANDS@ --help' -- "$cur") )
    elif [[ "$command" == completion ]]; then
        COMPREPLY=( $(compgen -W 'bash zsh fish' -- "$cur") )
    elif [[ "$command" == std ]]; then
        COMPREPLY=( $(compgen -W "$(helix std)" -- "$cur") )
    elif [[ "$cur" == --* ]]; then
        case "$command" in
            exec) COMPREPLY=( $(compgen -W '--json --yaml --help' -- "$cur") ) ;;
            watch) COMPREPLY=( $(compgen -W '--interval-ms --iterations --help' -- "$cur") ) ;;
            check) COMPREPLY=( $(compgen -W '--evaluate --help' -- "$cur") ) ;;
            step) COMPREPLY=( $(compgen -W '--steps --help' -- "$cur") ) ;;
            trace) COMPREPLY=( $(compgen -W '--limit --help' -- "$cur") ) ;;
            *) COMPREPLY=( $(compgen -W '--help' -- "$cur") ) ;;
        esac
    else
        COMPREPLY=()
        compopt -o default
    fi
}
complete -F _helix_complete helix
