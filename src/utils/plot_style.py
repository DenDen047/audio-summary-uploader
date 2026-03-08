import plotly.graph_objects as go


def plotly_scientific_style(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        template="plotly",
        font={"family": "Arial, sans-serif", "size": 14},
        width=1000,
        height=800,
    )
    return fig
