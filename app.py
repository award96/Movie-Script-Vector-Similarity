import os
from flask import Flask, render_template_string, request, jsonify
import polars as pl
import plotly.express as px
from plotly.utils import PlotlyJSONEncoder
import torch
import vector_search
import make_umap_plots
import json

# load the movie dataset
# omitting the actual scripts
movie_dataset = (
    pl.scan_parquet("data/out/movie-script-dataset.parquet")
      .with_columns( script_length = pl.col("script").str.len_chars() )
      .select(pl.col("index", "movie_title", "genre", "script_length", "year"))
      .collect()
)

# There are only 110 movie script embeddings
embeddings: torch.Tensor = torch.load("data/out/scripts-embedded.pt", weights_only=True)
# so we can pre-load all 110^2 comparisons
# for Distance, Dotproduct, and Cosine
# {"Distance": torch.Tensor of shape (n_movies, n_movies) ...}
similarity_name_value_pairs: dict[torch.Tensor] = \
    vector_search.calculate_all_similarity_pairs(embeddings)

# load UMAP reducer, reduce embeddings to 2D, join to movie dataset, add columns for visualization
umap_2d_embeddings: pl.DataFrame = make_umap_plots.reduce_data_and_add_vis_cols(embeddings, movie_dataset)

# randomly sorted titles for
# user to select from
titles_list = movie_dataset.select(pl.col("movie_title").shuffle())\
    ["movie_title"].to_list()
movie_titles = [{"id": t, "text": t} for t in titles_list]

# genres for user to select from
# alphabetically ordered
genres_list = (
    movie_dataset.lazy()
    .select(pl.col("genre").str.split(",").flatten())
    .unique()
    .sort(pl.col("genre"))
    .collect()
    ["genre"]
    .to_list()
)
genres = [{"id": g, "text": g} for g in genres_list]

# metric for main plot
# currently not variable
metric = "Distance"
IMAGE_FOLDER = os.path.join('data', 'out', 'plots')
# --------------------------
# Flask Application
# --------------------------
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = IMAGE_FOLDER
print()
print()
print()
print('-'*50*3)
"""
Homepage
"""
@app.route('/')
def index():
    movie_titles_json = json.dumps(movie_titles)
    with open('static/templates/index.html', 'r') as file:
       html_template = file.read()
    project_diagram_png_path = os.path.join(app.config['UPLOAD_FOLDER'], 'Project-Diagram.png')
    print(project_diagram_png_path)
    return render_template_string(
        html_template, 
        movie_titles_json=movie_titles_json, 
        project_diagram=project_diagram_png_path
    )

"""
Generate plots for homepage '/' route
"""
@app.route('/visualize', methods=['POST'])
def make_plots():
    # get movie title from search bar
    data = request.get_json()
    movie_title = data.get('movie_title')

    if not movie_title:
        return jsonify({"error": "No movie title provided"}), 400

    # Get all movies sorted by distance
    neighbors_df = vector_search.return_matches(
        movie_title, 
        similarity_name_value_pairs, 
        movie_dataset, 
        metric
    )
    # Create scatter plot for nearest neighbors
    fig_neighbors = px.scatter(
        neighbors_df,
        x="Distance",
        # visualize other similarity metrics
        y="Dotproduct",
        color="Cosine",
        text="movie_title",
        color_continuous_scale="Blues",
        size="Dotproduct",
        hover_data=["Dotproduct", "Cosine", "Distance", "movie_title"]
    )
    fig_neighbors.update_traces(textposition='top center')
    fig_neighbors.update_layout(
        title=f"Nearest Neighbors to {movie_title}"
    )
    # correlation between distance, dotproduct, and cosine
    correlation_df = neighbors_df.select(pl.col(pl.Float32, pl.Float64)).corr()

    # Create correlation heatmap
    fig_corr = px.imshow(
        correlation_df,
        labels=dict(x="Metric", y="Metric", color="Correlation"),
        x=["Dotproduct", "Cosine", "Distance"],
        y=["Dotproduct", "Cosine", "Distance"],
        color_continuous_scale="Portland"
    )
    fig_corr.update_layout(
        title=f"Similarity Metrics Correlation Heatmap<br>for {movie_title}"
    )

    return jsonify({
        "corr_plot": fig_corr.to_json(),
        "neighbors_plot": fig_neighbors.to_json()
    })

"""
UMAP 2D Plots
"""
@app.route('/umap_plots')
def umap_plots():
    with open('static/templates/umap_plots.html', 'r') as file:
       html_template = file.read()

    # List of Plotly Figure objects
    plotly_figs = make_umap_plots.make_all_visualizations(umap_2d_embeddings)  
    figs_json = [json.dumps(fig, cls=PlotlyJSONEncoder) for fig in plotly_figs]

    return render_template_string(html_template, figs=figs_json, genres=json.dumps(genres))

@app.route('/update_genre_plot', methods=['POST'])
def update_genre_plot():
    data = request.get_json()
    selected_genre = data.get('genre')

    # Generate the updated figure
    # Re-run some portion of make_umap_plots or just call genre_scatter_plot
    # But note that genre_scatter_plot picks a random focus from the splitted genres.
    # Let’s make a small refactor to accept `focus` from user:
    updated_fig = make_umap_plots.genre_scatter_plot(umap_2d_embeddings, selected_genre)

    return jsonify(json.dumps(updated_fig, cls=PlotlyJSONEncoder))

if __name__ == '__main__':
    app.run(debug=True)
