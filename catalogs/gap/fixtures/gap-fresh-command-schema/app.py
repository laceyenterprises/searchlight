def names(command, context):
    return [p.name for p in command.get_params(context) if p.name != "help"]
