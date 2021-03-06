define ['jquery', 'underscore', 'backbone', 'lib/text!app/templates/league_template.ejs'], ($, _, Backbone, league_template) ->
  class League extends Backbone.Model

  class EspnLeagues extends Backbone.Collection

    getUrl: ->
      console.log "getting url"
      if window.global_leagues then '/global_leagues' else '/leagues'

    url: @prototype.getUrl,

    model: League

    initialize: ->
      _.bindAll @, 'fetch'

  class LeagueView extends Backbone.View

    tagName: 'tr'

    template: _.template(league_template)

    initialize: ->
      @listenTo @model, 'change', @render

    render: ->
      model = @model.toJSON()
      console.log model
      model.percent_done = (100 * model.pages_scraped) / model.total_pages
      @$el.html(@template(model))
      @

  class AppView extends Backbone.View

    el : $("#accounts_app")

    initialize: ->
      @listenTo @collection, 'add', @addOne
      @collection.fetch()

      @collection.on 'sync', =>
        isLoaded = (model) ->
          model.get('loaded')

        if (_.isEmpty (@collection.models)) or not (_.every @collection.models, isLoaded)
          setTimeout @collection.fetch, 5000

    addOne: (league) ->
      $("#user-loaded").html("True")
      leagueView = new LeagueView { model: league }
      $('#accounts_list').append(leagueView.render().el)

  League: League
  EspnLeagues: EspnLeagues
  LeagueView: LeagueView
  AppView: AppView












