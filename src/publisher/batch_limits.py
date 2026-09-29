"""Publisher batch creation limit; historical batches remain untouched."""

MAX_DRAFTS_PER_BATCH = 30
MAX_DRAFTS_PER_EXECUTION = 100


class BatchLimitExceeded(ValueError):
    def __init__(self, actual):
        self.code = 'BATCH_LIMIT_EXCEEDED'
        self.actual = int(actual)
        self.max = MAX_DRAFTS_PER_BATCH
        super().__init__(f'{self.code}: {self.actual} selecionados; máximo {self.max} rascunhos por lote')


def require_batch_size(count):
    if count < 1:
        raise ValueError('Selecione pelo menos um vídeo para o lote')
    if count > MAX_DRAFTS_PER_BATCH:
        raise BatchLimitExceeded(count)
