"""Only documented request text surfaces; metadata and duplicate request trees ignored."""


def extract(event):
    texts=[]
    coverage=dict(text_parts=0,unsupported_parts=0,truncated_parts=0)
    def add(value):
        if len(texts)>=1000:
            coverage['truncated_parts']+=1
            return
        if len(value)>100000:
            coverage['truncated_parts']+=1
        texts.append(value[:100000])
        coverage['text_parts']+=1
    def content(value):
        if isinstance(value,str):
            add(value)
        elif isinstance(value,list):
            for part in value[:1000]:
                if isinstance(part,dict) and part.get('type') in ('text','input_text','output_text') and isinstance(part.get('text'),str):
                    add(part['text'])
                else:
                    coverage['unsupported_parts']+=1
            if len(value)>1000:
                coverage['truncated_parts']+=1
        elif value is not None:
            coverage['unsupported_parts']+=1
    # request_messages is canonical. text is solely the synthetic adapter surface.
    messages=event.get('request_messages')
    if isinstance(messages,list):
        for message in messages[:1000]:
            if isinstance(message,dict):
                content(message.get('content'))
            else:
                coverage['unsupported_parts']+=1
        if len(messages)>1000:
            coverage['truncated_parts']+=1
    else:
        content(event.get('text'))
    content(event.get('system_prompt'))
    return texts,coverage
