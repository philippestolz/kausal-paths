from typing import Iterable
from dataclasses import dataclass
from .constants import FORECAST_COLUMN, VALUE_COLUMN, YEAR_COLUMN


@dataclass
class Dataset:
    id: str
    input_dataset: str = None
    column: str = None
    filters: Iterable = None
    groupby: dict = None

    def load(self, context):
        if self.input_dataset:
            df = context.load_dataset(self.input_dataset)
            if self.filters:
                for d in self.filters:
                    col = d['column']
                    val = d['value']
                    df = df[df[col] == val]

            if self.groupby:
                g = self.groupby
                df = df.groupby([g['index_column'], g['columns_from']])[g['value_column']].sum()
                df = df.unstack(g['columns_from'])

            return df

        df = context.load_dataset(self.id)
        cols = df.columns
        if self.column:
            if self.column not in cols:
                available = ', '.join(cols)
                raise Exception(
                    "Column '%s' not found in dataset '%s'. Available columns: %s" % (
                        self.column, self.id, available
                    )
                )
            assert self.column in cols
            if YEAR_COLUMN in cols:
                df = df.set_index(YEAR_COLUMN)
            if FORECAST_COLUMN in cols:
                df = df.rename(columns={self.column: VALUE_COLUMN})
                cols = [VALUE_COLUMN, FORECAST_COLUMN]
            else:
                return df[self.column]

        return df[cols]
