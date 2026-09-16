from config.settings import config


def initialize_state_var():

    PROJECT = config.BQ_PROJECT_ID
    BQ_LOCATION = config.LOCATION
    DATASET = config.BQ_DATASET_ID

    return {
        "PROJECT": PROJECT,
        "BQ_LOCATION": BQ_LOCATION,
        "DATASET": DATASET
    }


