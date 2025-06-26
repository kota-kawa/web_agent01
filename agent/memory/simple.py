from ..utils.history import load_hist, save_hist


def get_memory():
    return load_hist()


def update_memory(item):
    h = load_hist()
    h.append(item)
    save_hist(h)
    return h
